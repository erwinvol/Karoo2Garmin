"""Unit tests for FitEnhancer in Karoo2Garmin."""

import tempfile
import unittest
from pathlib import Path

from src.fit_enhancer import EDGE_1040_PRODUCT_ID, FitEnhancer, _calc_np


class TestFitEnhancer(unittest.TestCase):
    def test_calc_np_empty(self):
        self.assertEqual(_calc_np([]), 0.0)

    def test_calc_np_constant(self):
        powers = [220.0] * 50
        self.assertAlmostEqual(_calc_np(powers), 220.0, places=1)

    def test_calc_np_variable(self):
        powers = ([120.0] * 60 + [320.0] * 60) * 5
        self.assertGreater(_calc_np(powers), 220.0)

    def test_calc_np_smooths_with_30s_rolling_average(self):
        # Alternating 1-second spikes average out over 30 seconds
        powers = [120.0, 320.0] * 50
        self.assertAlmostEqual(_calc_np(powers), 220.0, delta=5.0)

    def test_disabled_spoofing_returns_original_path(self):
        enhancer = FitEnhancer(spoof_device=False)
        with tempfile.NamedTemporaryFile(suffix=".fit") as tmp:
            result = enhancer.enhance(tmp.name)
            self.assertEqual(result, tmp.name)

    def test_nonexistent_file_raises_error(self):
        enhancer = FitEnhancer()
        with self.assertRaises(FileNotFoundError):
            enhancer.enhance("/nonexistent/path/to/ride.fit")

    def test_enhance_ungrowable_device_info(self):
        try:
            from fit_tool.definition_message import DefinitionMessage
            from fit_tool.fit_file import FitFile
            from fit_tool.fit_file_header import FitFileHeader
            from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
            from fit_tool.profile.messages.file_id_message import FileIdMessage
            from fit_tool.profile.profile_type import FileType, Manufacturer
            from fit_tool.record import Record
        except ImportError:
            return  # Skip if fit-tool is unavailable

        file_id = FileIdMessage()
        file_id.type = FileType.ACTIVITY
        file_id.manufacturer = 255
        file_id.product = 999

        dev = DeviceInfoMessage()
        dev.manufacturer = 255
        dev.product = 999

        records = [
            Record.from_message(DefinitionMessage.from_data_message(file_id)),
            Record.from_message(file_id),
            Record.from_message(DefinitionMessage.from_data_message(dev)),
            Record.from_message(dev),
        ]
        rec_bytes = b"".join(r.to_bytes() for r in records)
        header = FitFileHeader(records_size=len(rec_bytes))
        fit = FitFile(header, records)

        with tempfile.NamedTemporaryFile(suffix=".fit", delete=False) as tmp:
            fit.to_file(tmp.name)
            src_path = tmp.name

        enhancer = FitEnhancer(device_product_id=EDGE_1040_PRODUCT_ID)
        out_path = enhancer.enhance(src_path)

        # Must not have fallen back to source path due to ungrowable error
        self.assertNotEqual(out_path, src_path)

        enhanced_fit = FitFile.from_file(out_path)
        found_dev = False
        found_file_id = False
        for r in enhanced_fit.records:
            if isinstance(r.message, DeviceInfoMessage):
                self.assertEqual(r.message.manufacturer, 1)
                self.assertEqual(r.message.product, EDGE_1040_PRODUCT_ID)
                found_dev = True
            elif isinstance(r.message, FileIdMessage):
                self.assertEqual(r.message.manufacturer, 1)
                self.assertEqual(r.message.product, EDGE_1040_PRODUCT_ID)
                found_file_id = True

        self.assertTrue(found_dev)
        self.assertTrue(found_file_id)

    def test_cleanup_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
        self.assertTrue(Path(tmp_path).exists())
        FitEnhancer.cleanup_file(tmp_path)
        self.assertFalse(Path(tmp_path).exists())


def _karoo_style_fit(path: str) -> None:
    """Write a small ride shaped like a Karoo file: Hammerhead creator entries repeated,
    a sensor entry, 1 Hz records with a dropped power sample, and big-endian lap, session
    and activity messages without the summary fields a Garmin device writes. The lap
    carries repetition_num and the session a developer field, as Karoo files do."""
    from fit_tool.base_type import BaseType
    from fit_tool.definition_message import DefinitionMessage
    from fit_tool.developer_field import DeveloperField
    from fit_tool.endian import Endian
    from fit_tool.fit_file import FitFile
    from fit_tool.fit_file_header import FitFileHeader
    from fit_tool.profile.messages.activity_message import ActivityMessage
    from fit_tool.profile.messages.developer_data_id_message import DeveloperDataIdMessage
    from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
    from fit_tool.profile.messages.field_description_message import FieldDescriptionMessage
    from fit_tool.profile.messages.file_id_message import FileIdMessage
    from fit_tool.profile.messages.lap_message import LapMessage
    from fit_tool.profile.messages.record_message import RecordMessage
    from fit_tool.profile.messages.session_message import SessionMessage
    from fit_tool.profile.profile_type import FileType, Sport
    from fit_tool.record import Record

    start_ms = 1_760_000_000_000
    seconds = 120
    hammerhead = 289

    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    file_id.manufacturer = hammerhead
    file_id.product = 2
    file_id.time_created = start_ms

    messages = [file_id]
    for battery_update in range(2):
        creator = DeviceInfoMessage(local_id=1)
        creator.timestamp = start_ms + battery_update * 60_000
        creator.device_index = 0
        creator.manufacturer = hammerhead
        creator.product = 2
        messages.append(creator)
    sensor = DeviceInfoMessage(local_id=1)
    sensor.timestamp = start_ms
    sensor.device_index = 1
    sensor.manufacturer = 32  # a Wahoo heart-rate strap
    sensor.product = 5
    messages.append(sensor)

    for second in range(seconds):
        record = RecordMessage(local_id=2)
        record.timestamp = start_ms + second * 1000
        record.position_lat = 40.0 + second * 0.0001
        record.position_long = -75.0 - second * 0.0001
        record.heart_rate = 140
        record.power = 200
        if second == 10:
            record.get_field_by_name("power").set_encoded_value(0, 0xFFFF, check_validity=False)
        messages.append(record)

    end_ms = start_ms + (seconds - 1) * 1000
    lap = LapMessage(local_id=3, endian=Endian.BIG)
    lap.timestamp = end_ms
    lap.start_time = start_ms
    lap.total_elapsed_time = seconds
    lap.total_timer_time = seconds
    lap.repetition_num = 1
    messages.append(lap)

    dev_id = DeveloperDataIdMessage(local_id=6)
    dev_id.developer_data_index = 0
    dev_id.application_id = list(range(16))
    description = FieldDescriptionMessage(local_id=7)
    description.developer_data_index = 0
    description.field_definition_number = 0
    description.fit_base_type_id = BaseType.UINT16.value
    description.field_name = "radar_cars"
    messages += [dev_id, description]

    session = SessionMessage(
        local_id=4,
        endian=Endian.BIG,
        developer_fields=[DeveloperField(field_id=0, name="radar_cars", base_type=BaseType.UINT16,
                                         size=2, growable=True, developer_data_index=0)],
    )
    session.timestamp = end_ms
    session.start_time = start_ms
    session.sport = Sport.CYCLING
    session.total_elapsed_time = seconds
    session.total_timer_time = seconds
    session.developer_fields[0].set_value(0, 17)
    messages.append(session)

    activity = ActivityMessage(local_id=5, endian=Endian.BIG)
    activity.timestamp = end_ms
    activity.total_timer_time = seconds
    messages.append(activity)

    records, defined = [], {}
    for message in messages:
        definition = DefinitionMessage.from_data_message(message)
        if defined.get(message.local_id) != definition.to_bytes():
            records.append(Record.from_message(definition))
            defined[message.local_id] = definition.to_bytes()
        records.append(Record.from_message(message))
    size = sum(len(r.to_bytes()) for r in records)
    FitFile(FitFileHeader(records_size=size), records).to_file(path)


class TestKarooStyleFile(unittest.TestCase):
    """Garmin builds the activity summary it syncs to watches from these messages; a
    Karoo file's own versions make a Garmin watch reboot when the ride is opened."""

    @classmethod
    def setUpClass(cls):
        try:
            from fit_tool.fit_file import FitFile
        except ImportError:
            raise unittest.SkipTest("fit-tool is unavailable")
        cls.tmp = tempfile.TemporaryDirectory()
        src = str(Path(cls.tmp.name) / "karoo.fit")
        _karoo_style_fit(src)
        cls.out = FitEnhancer(athlete_ftp=250).enhance(src, str(Path(cls.tmp.name) / "out.fit"))
        cls.messages = [r.message for r in FitFile.from_file(cls.out).records]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def of_type(self, name):
        return [m for m in self.messages if type(m).__name__ == name]

    def test_enhanced_without_falling_back(self):
        self.assertTrue(self.out.endswith("out.fit"))

    def test_every_creator_entry_is_the_edge(self):
        creators = [m for m in self.of_type("DeviceInfoMessage") if m.device_index == 0]
        self.assertEqual(len(creators), 2)
        for creator in creators:
            self.assertEqual(creator.manufacturer, 1)
            self.assertEqual(creator.product, EDGE_1040_PRODUCT_ID)
            self.assertAlmostEqual(creator.software_version, 27.1)

    def test_sensor_entry_is_untouched(self):
        sensors = [m for m in self.of_type("DeviceInfoMessage") if m.device_index == 1]
        self.assertEqual([(s.manufacturer, s.product) for s in sensors], [(32, 5)])

    def test_file_creator_added(self):
        (creator,) = self.of_type("FileCreatorMessage")
        self.assertEqual(creator.software_version, 2710)

    def test_lap_has_garmin_fields(self):
        (lap,) = self.of_type("LapMessage")
        self.assertEqual(lap.message_index, 0)
        self.assertEqual(lap.event, 9)  # lap
        self.assertEqual(lap.event_type, 1)  # stop
        self.assertEqual(lap.sport, 2)  # cycling
        self.assertEqual(lap.intensity, 0)  # active
        self.assertAlmostEqual(lap.start_position_lat, 40.0, places=4)
        self.assertAlmostEqual(lap.end_position_long, -75.0119, places=4)
        self.assertIsNone(lap.repetition_num)

    def test_session_has_garmin_fields(self):
        (session,) = self.of_type("SessionMessage")
        self.assertEqual(session.event, 8)  # session
        self.assertEqual(session.event_type, 1)  # stop
        self.assertEqual(session.first_lap_index, 0)
        self.assertEqual(session.num_laps, 1)
        self.assertEqual(session.message_index, 0)
        self.assertAlmostEqual(session.nec_lat, 40.0119, places=4)
        self.assertAlmostEqual(session.swc_long, -75.0119, places=4)
        self.assertEqual(session.developer_fields, [])

    def test_session_power_ignores_dropped_samples(self):
        (session,) = self.of_type("SessionMessage")
        self.assertEqual(session.avg_power, 200)
        self.assertEqual(session.max_power, 200)
        self.assertEqual(session.normalized_power, 200)
        self.assertAlmostEqual(session.training_stress_score, 2.1, places=1)
        self.assertEqual(session.total_work, 24000)

    def test_activity_counts_sessions(self):
        (activity,) = self.of_type("ActivityMessage")
        self.assertEqual(activity.num_sessions, 1)
        self.assertIsNotNone(activity.local_timestamp)

    def test_ride_data_kept(self):
        self.assertEqual(len(self.of_type("RecordMessage")), 120)


if __name__ == "__main__":
    unittest.main()
