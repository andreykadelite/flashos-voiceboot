import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from audio import parse_outputs


def node(id_, name, **props):
    return {'id': id_, 'type': 'PipeWire:Interface:Node', 'info': {'props': {
        'node.name': name, 'media.class': 'Audio/Sink', 'device.api': 'alsa', **props}}}


class AudioTests(unittest.TestCase):
    def test_usb_then_analog_then_hdmi(self):
        objects = [node(1, 'alsa_output.hdmi'), node(2, 'alsa_output.analog'),
                   node(3, 'alsa_output.usb', **{'device.id': 20}),
                   {'id': 20, 'info': {'props': {'device.bus': 'usb'}}}]
        self.assertEqual([o.id for o in parse_outputs(objects)], [3, 2, 1])

    def test_dummy_and_capture_nodes_do_not_claim_working_audio(self):
        objects = [node(1, 'auto_null'), node(2, 'virtual', **{'node.virtual': True}),
                   node(3, 'microphone', **{'media.class': 'Audio/Source'}),
                   node(4, 'physical')]
        self.assertEqual([o.name for o in parse_outputs(objects)], ['physical'])

    def test_optical_outputs_do_not_outrank_speakers(self):
        objects = [node(1, 'aaa', **{'node.description': 'Digital Stereo (IEC958)'}),
                   node(2, 'HiFi__SPDIF__sink'), node(3, 'speakers')]
        self.assertEqual([o.rank for o in parse_outputs(objects)], [1, 2, 2])
        self.assertEqual(parse_outputs(objects)[0].id, 3)

    def test_partial_hotplug_records_are_ignored(self):
        objects = [None, {}, {'id': 1, 'info': None}, node(2, None), node(3, 'ready')]
        self.assertEqual([o.name for o in parse_outputs(objects)], ['ready'])
        with self.assertRaises(ValueError):
            parse_outputs({'error': 'not a graph'})
