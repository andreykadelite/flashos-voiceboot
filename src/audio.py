"""PipeWire output discovery. WirePlumber owns UCM profiles and jack routing."""
from dataclasses import dataclass
import json
import subprocess


@dataclass(frozen=True)
class Output:
    name: str
    serial: str
    id: int
    title: str
    rank: int


def parse_outputs(objects):
    if not isinstance(objects, list):
        raise ValueError('Unexpected PipeWire response')
    props_by_id = {}
    for obj in objects:
        if isinstance(obj, dict) and isinstance(obj.get('info'), dict):
            props = obj['info'].get('props')
            if isinstance(props, dict):
                props_by_id[str(obj.get('id'))] = props
    outputs = []
    for obj in objects:
        if not isinstance(obj, dict) or obj.get('type') != 'PipeWire:Interface:Node':
            continue
        props = props_by_id.get(str(obj.get('id')), {})
        if props.get('media.class') != 'Audio/Sink':
            continue
        name = props.get('node.name')
        node_id = obj.get('id')
        if not isinstance(name, str) or not name or len(name) > 512 or type(node_id) is not int:
            continue
        # A dummy sink accepts samples without producing any physical sound.
        if name == 'auto_null' or str(props.get('node.virtual', '')).lower() == 'true':
            continue
        device = props_by_id.get(str(props.get('device.id')), {})
        if props.get('device.api', device.get('device.api')) != 'alsa':
            continue
        bus = props.get('device.bus', device.get('device.bus', ''))
        description = str(props.get('node.description') or props.get('node.nick') or name)
        label = (name + ' ' + description).lower()
        digital = any(token in label for token in ('hdmi', 'iec958', 'spdif', 's/pdif'))
        rank = 0 if bus == 'usb' else 2 if digital else 1
        title = ' '.join(description.split())[:140]
        outputs.append(Output(name, str(props.get('object.serial', node_id)), node_id, title, rank))
    return sorted(outputs, key=lambda item: (item.rank, item.name))


class Audio:
    def discover(self):
        result = subprocess.run(['pw-dump'], check=True, capture_output=True, text=True, timeout=3)
        return parse_outputs(json.loads(result.stdout))

    def activate(self, output):
        for setting, value in [('set-mute', '0'), ('set-volume', '0.80')]:
            subprocess.run(['wpctl', setting, str(output.id), value], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)

    @staticmethod
    def player(output, volume):
        return ['pw-play', '--target=' + output.name, '--media-role=Accessibility',
                '--volume=' + str(volume / 100), '-']
