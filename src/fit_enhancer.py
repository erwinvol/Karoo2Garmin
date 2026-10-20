"""FIT file device enhancer to enable Garmin Physio TrueUp and training metrics for Karoo rides."""

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Garmin product IDs from the FIT SDK profile (garmin_product).
EDGE_1040_PRODUCT_ID = 3843
EDGE_840_PRODUCT_ID = 4062

# FIT timestamps count seconds from 1989-12-31T00:00:00Z.
_FIT_EPOCH_UNIX_S = 631065600

# Lap fields a Karoo writes that no Garmin cycling computer does: repetition_num belongs to
# strength and pool workouts.
_KAROO_ONLY_LAP_FIELDS = frozenset({61})


def _calc_np(power_values: List[float]) -> float:
    """Calculate Normalized Power (NP) from 1-second samples, zeros included:
    30-second rolling average, 4th power average of that, then 4th root."""
    if not power_values:
        return 0.0
    window = min(30, len(power_values))
    total = sum(power_values[:window])
    rolling = [total / window]
    for i in range(window, len(power_values)):
        total += power_values[i] - power_values[i - window]
        rolling.append(total / window)
    avg_4th = sum(p**4 for p in rolling) / len(rolling)
    return avg_4th**0.25


def _safe_set(obj: object, attr: str, val: object) -> None:
    """Set attribute on object safely ignoring missing attributes."""
    try:
        setattr(obj, attr, val)
    except Exception:
        pass


def _has_value(field) -> bool:
    """True when a decoded field holds a real value.

    fit-tool decodes FIT's "invalid" marker (e.g. 0xFFFF for a power sample the
    sensor dropped) as an ordinary number, so it has to be filtered here.
    """
    if field is None or not field.is_valid():
        return False
    invalid = field.base_type.invalid_raw_value()
    return any(v not in (None, "") and v != invalid for v in field.encoded_values)


def _value(message, name: str):
    """A field's decoded value, or None when it is missing or holds the invalid marker."""
    field = message.get_field_by_name(name)
    return field.get_value() if _has_value(field) else None


def _rebuilt(record, skip_fields: frozenset = frozenset()):
    """A copy of a decoded record's message whose fields can grow.

    Decoded messages are tied to the file's own definition, so a field the Karoo did not
    write cannot be set on them ("Field is not growable"). The copy starts blank, so any
    field can be added; it keeps every original value except ``skip_fields`` and leaves
    out developer (extension) fields. The local message number comes from the record,
    as fit-tool before 0.9.16 leaves it at 0 on decoded messages.
    """
    message = record.message
    copy = type(message)(local_id=record.local_id, endian=message.endian)
    for field in message.fields:
        if field.field_id in skip_fields or not _has_value(field):
            continue
        target = copy.get_field(field.field_id)
        if target is None:
            continue
        for index, value in enumerate(field.encoded_values):
            target.set_encoded_value(index, value)
    return copy


class FitEnhancer:
    """Modifies Karoo FIT file headers and device signatures to unlock Garmin training load/effect.

    Besides the device signature, the activity, session and lap messages are rebuilt the way
    a Garmin device writes them. Garmin Connect builds the summary it syncs to a Garmin watch
    (Physio TrueUp) straight from these messages, and the Karoo's own version leaves out
    fields such as the session count, first lap index and lap indexes while adding
    developer fields and a lap repetition count. Opening such a ride in the watch's
    activity history reboots the watch (seen on an Enduro 3, firmware 23.39).
    """

    def __init__(
        self,
        spoof_device: bool = True,
        device_product_id: int = EDGE_1040_PRODUCT_ID,  # Edge 1040 (default), 4062 for Edge 840
        software_version: float = 27.10,
        athlete_ftp: float = 0.0,
    ):
        self.spoof_device = spoof_device
        self.device_product_id = device_product_id
        self.software_version = software_version
        self.athlete_ftp = athlete_ftp

    def enhance(self, input_path: str, output_path: Optional[str] = None) -> str:
        """Enhance a Karoo FIT file with Garmin Edge device signatures.

        Preserves:
            - Real-world GPS trackpoints and coordinates
            - Barometric elevation and temperatures
            - ANT+ / Bluetooth sensor pairings (HR monitors, power meters, radar)
            - Electronic gear shifting data (SRAM AXS, Shimano Di2)
            - Sport / SubSport classification (Road, Gravel, MTB, Indoor)
            - Developer (extension) data in records

        Args:
            input_path: Path to the original Karoo FIT file.
            output_path: Optional destination path. If not provided, a temp file is used.

        Returns:
            Path to the output FIT file (modified if enabled, or original if bypassed).
        """
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Source FIT file not found: {input_path}")

        if not self.spoof_device:
            logger.info("Device spoofing disabled; using original Karoo FIT file.")
            return input_path

        try:
            from fit_tool.definition_message import DefinitionMessage
            from fit_tool.fit_file import FitFile
            from fit_tool.fit_file_header import FitFileHeader
            from fit_tool.record import Record
            from fit_tool.profile.messages.activity_message import ActivityMessage
            from fit_tool.profile.messages.file_id_message import FileIdMessage
            from fit_tool.profile.messages.file_creator_message import FileCreatorMessage
            from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
            from fit_tool.profile.messages.sport_message import SportMessage
            from fit_tool.profile.messages.record_message import RecordMessage
            from fit_tool.profile.messages.lap_message import LapMessage
            from fit_tool.profile.messages.session_message import SessionMessage
            from fit_tool.profile.profile_type import (
                Event,
                EventType,
                FileType,
                Manufacturer,
                Sport,
                SubSport,
                Intensity,
                SessionTrigger,
            )
        except ImportError as exc:
            logger.warning(
                "fit-tool library not available (%s); uploading original file without spoofing.",
                exc,
            )
            return input_path

        dest_path = output_path or os.path.join(
            tempfile.gettempdir(), f"enhanced_{os.path.basename(input_path)}"
        )

        logger.info(
            "Patching Karoo FIT file %s -> Garmin Edge (Product ID %d, SW %.2f)...",
            os.path.basename(input_path),
            self.device_product_id,
            self.software_version,
        )

        garmin = Manufacturer.GARMIN.value if hasattr(Manufacturer.GARMIN, "value") else 1

        try:
            with open(input_path, "rb") as f:
                raw_bytes = f.read()

            fit_file = FitFile.from_bytes(raw_bytes, check_crc=False)

            # Pass 1: positions for the map bounding box and lap start/end, and the
            # session count, which the activity message must carry.
            fixes: List[Tuple[int, float, float]] = []
            sessions = 0
            for record in fit_file.records:
                msg = record.message
                if type(msg) is RecordMessage:
                    lat, lon, ts = _value(msg, "position_lat"), _value(msg, "position_long"), msg.timestamp
                    if lat is not None and lon is not None and ts is not None:
                        fixes.append((ts, lat, lon))
                elif type(msg) is SessionMessage:
                    sessions += 1

            def position(start: Optional[int], end: Optional[int], last: bool):
                if start is None or end is None:
                    return None, None
                inside = [(lat, lon) for ts, lat, lon in fixes if start <= ts <= end]
                if not inside:
                    return None, None
                return inside[-1] if last else inside[0]

            cadence_vals: List[float] = []
            power_vals: List[float] = []
            hr_vals: List[float] = []
            all_cadence: List[float] = []
            all_power: List[float] = []
            power_samples: List[float] = []  # every sample, zeros included, for NP
            all_hr: List[float] = []

            has_file_creator = any(type(r.message) is FileCreatorMessage for r in fit_file.records)
            latest_definition: Dict[int, object] = {}
            lap_index = 0
            out_records = []

            def replace(original, rebuilt) -> None:
                """Write ``rebuilt`` with its own definition, then restore the original one
                for any later message on the same local message number."""
                out_records.append(Record.from_message(DefinitionMessage.from_data_message(rebuilt)))
                out_records.append(Record.from_message(rebuilt))
                if original.local_id in latest_definition:
                    out_records.append(latest_definition[original.local_id])

            for record in fit_file.records:
                msg = record.message
                msg_type = type(msg)

                if msg_type is DefinitionMessage:
                    latest_definition[record.local_id] = record
                    out_records.append(record)
                    continue

                # 1. FileId: Spoof file creator as Garmin Edge
                if msg_type is FileIdMessage:
                    _safe_set(msg, "type", FileType.ACTIVITY)
                    _safe_set(msg, "manufacturer", garmin)
                    _safe_set(msg, "product", self.device_product_id)
                    out_records.append(record)
                    # Every Garmin device writes its firmware version right after file_id.
                    if not has_file_creator:
                        creator = FileCreatorMessage(local_id=15)
                        creator.software_version = round(self.software_version * 100)
                        out_records.append(Record.from_message(DefinitionMessage.from_data_message(creator)))
                        out_records.append(Record.from_message(creator))
                        if 15 in latest_definition:
                            out_records.append(latest_definition[15])
                        has_file_creator = True
                    continue

                # 2. DeviceInfo: Spoof the recording unit in every entry (the Karoo repeats it
                # with each battery update), preserving external ANT+/BLE sensors & SRAM AXS
                if msg_type is DeviceInfoMessage:
                    if _value(msg, "device_index") in (0, None):
                        device = _rebuilt(record)
                        device.manufacturer = garmin
                        device.product = self.device_product_id
                        device.software_version = self.software_version
                        replace(record, device)
                    else:
                        out_records.append(record)
                    continue

                # 3. Sport: Preserve Karoo's sport and sub_sport (do NOT force Virtual Activity!)
                if msg_type is SportMessage:
                    if not _value(msg, "sport"):
                        _safe_set(msg, "sport", Sport.CYCLING)
                    out_records.append(record)
                    continue

                # 4. Records: Collect metrics while preserving GPS and sensor data
                if msg_type is RecordMessage:
                    c = _value(msg, "cadence")
                    if c and c > 0:
                        cadence_vals.append(float(c))
                        all_cadence.append(float(c))

                    p = _value(msg, "power")
                    if p is not None:
                        power_samples.append(float(p))
                    if p and p > 0:
                        power_vals.append(float(p))
                        all_power.append(float(p))

                    hr = _value(msg, "heart_rate")
                    if hr and hr > 0:
                        hr_vals.append(float(hr))
                        all_hr.append(float(hr))
                    out_records.append(record)
                    continue

                # 5. Lap: Rebuild with the fields a Garmin device writes, fill averages if missing
                if msg_type is LapMessage:
                    lap = _rebuilt(record, skip_fields=_KAROO_ONLY_LAP_FIELDS)
                    lap.event = Event.LAP
                    lap.event_type = EventType.STOP
                    lap.message_index = lap_index
                    if lap.sport is None:
                        lap.sport = Sport.CYCLING
                    if lap.sub_sport is None:
                        lap.sub_sport = SubSport.GENERIC
                    lap.intensity = Intensity.ACTIVE
                    if not lap.avg_cadence and cadence_vals:
                        lap.avg_cadence = round(sum(cadence_vals) / len(cadence_vals))
                    if not lap.avg_power and power_vals:
                        lap.avg_power = round(sum(power_vals) / len(power_vals))
                    if not lap.max_power and power_vals:
                        lap.max_power = round(max(power_vals))
                    if not lap.avg_heart_rate and hr_vals:
                        lap.avg_heart_rate = round(sum(hr_vals) / len(hr_vals))
                    if not lap.max_heart_rate and hr_vals:
                        lap.max_heart_rate = round(max(hr_vals))
                    lap_timer = lap.total_timer_time or lap.total_elapsed_time
                    if lap.avg_power and lap_timer:
                        lap.total_work = int(lap.avg_power * lap_timer)
                        if lap.total_calories is None:
                            lap.total_calories = round(lap.total_work / 1000)  # kcal ~ kJ of work
                    start_lat, start_lon = position(lap.start_time, lap.timestamp, last=False)
                    end_lat, end_lon = position(lap.start_time, lap.timestamp, last=True)
                    if start_lat is not None:
                        lap.start_position_lat, lap.start_position_long = start_lat, start_lon
                        lap.end_position_lat, lap.end_position_long = end_lat, end_lon
                    replace(record, lap)
                    lap_index += 1
                    cadence_vals, power_vals, hr_vals = [], [], []
                    continue

                # 6. Session: Rebuild with the fields a Garmin device writes & calculate training metrics
                if msg_type is SessionMessage:
                    session = _rebuilt(record)
                    if not session.sport:
                        session.sport = Sport.CYCLING
                    if session.sub_sport is None:
                        session.sub_sport = SubSport.GENERIC
                    session.event = Event.SESSION
                    session.event_type = EventType.STOP
                    session.trigger = SessionTrigger.ACTIVITY_END
                    if session.message_index is None:
                        session.message_index = 0
                    if session.first_lap_index is None:
                        session.first_lap_index = 0
                    if session.num_laps is None:
                        session.num_laps = max(lap_index, 1)

                    if not session.avg_cadence and all_cadence:
                        session.avg_cadence = round(sum(all_cadence) / len(all_cadence))
                    if not session.avg_power and all_power:
                        session.avg_power = round(sum(all_power) / len(all_power))
                    if not session.max_power and all_power:
                        session.max_power = round(max(all_power))
                    if not session.avg_heart_rate and all_hr:
                        session.avg_heart_rate = round(sum(all_hr) / len(all_hr))
                    if not session.max_heart_rate and all_hr:
                        session.max_heart_rate = round(max(all_hr))

                    # Total work (Joules = avg_power * timer_seconds)
                    timer_sec = session.total_timer_time or session.total_elapsed_time
                    avg_p = session.avg_power
                    if avg_p and timer_sec and avg_p > 0 and timer_sec > 0:
                        session.total_work = int(avg_p * timer_sec)
                        if session.total_calories is None:
                            session.total_calories = round(session.total_work / 1000)  # kcal ~ kJ

                    # Map bounding box
                    if fixes:
                        session.nec_lat = max(lat for _, lat, _ in fixes)
                        session.nec_long = max(lon for _, _, lon in fixes)
                        session.swc_lat = min(lat for _, lat, _ in fixes)
                        session.swc_long = min(lon for _, _, lon in fixes)

                    # Normalized Power, IF, and TSS if FTP provided and the Karoo didn't
                    # record them (it does when it has an FTP; its values are kept)
                    if self.athlete_ftp > 0 and power_samples and session.training_stress_score is None:
                        np_val = session.normalized_power or _calc_np(power_samples)
                        intensity_factor = np_val / self.athlete_ftp
                        session.normalized_power = round(np_val)
                        session.intensity_factor = round(intensity_factor, 3)
                        if timer_sec and timer_sec > 0:
                            tss = (timer_sec * np_val * intensity_factor) / (self.athlete_ftp * 3600) * 100
                            session.training_stress_score = round(tss, 1)
                    replace(record, session)
                    continue

                # 7. Activity: the session count, and local time like a Garmin device
                if msg_type is ActivityMessage:
                    activity = _rebuilt(record)
                    activity.num_sessions = max(sessions, 1)
                    if activity.timestamp is not None and activity.local_timestamp is None:
                        # timestamp reads as Unix milliseconds; local_timestamp is raw FIT
                        # seconds (since 1989-12-31) in local time
                        unix_s = activity.timestamp // 1000
                        offset = datetime.fromtimestamp(unix_s).astimezone().utcoffset()
                        activity.local_timestamp = unix_s - _FIT_EPOCH_UNIX_S + int(offset.total_seconds())
                    replace(record, activity)
                    continue

                out_records.append(record)

            fit_file.records = out_records
            # The file grew, so its header needs the new data size (fit-tool before
            # 0.9.16 doesn't update it on save)
            header = fit_file.header
            header.records_size = sum(len(r.to_bytes()) for r in out_records)
            if header.crc is not None:
                header.crc = FitFileHeader.generate_crc(
                    header.protocol_version, header.profile_version, header.records_size
                )
            fit_file.crc = None  # Force CRC recalculation on save
            fit_file.to_file(dest_path)
            logger.info("Successfully enhanced Karoo FIT file saved to %s", dest_path)
            return dest_path

        except Exception as exc:
            logger.exception("Failed to modify FIT file: %s. Using original file.", exc)
            return input_path

    @staticmethod
    def cleanup_file(path: Optional[str]) -> None:
        """Safely delete a temporary file."""
        if not path:
            return
        try:
            p = Path(path)
            if p.exists() and p.is_file():
                p.unlink()
                logger.debug("Cleaned up temporary file: %s", path)
        except OSError as exc:
            logger.warning("Could not delete temporary file %s: %s", path, exc)
