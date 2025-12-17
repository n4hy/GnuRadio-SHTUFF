
import yaml
from xml.etree.ElementTree import Element, SubElement, tostring, parse
from xml.dom import minidom
import datetime
import argparse

def convert_yaml_to_grc(yaml_file, grc_file):
    """
    Converts a GRC YAML file to a GRC XML file.
    """
    try:
        with open(yaml_file, 'r') as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Error: Input file not found at {yaml_file}")
        return
    except yaml.YAMLError as e:
        print(f"Error parsing YAML file: {e}")
        return

    flow_graph = Element('flow_graph')
    timestamp = SubElement(flow_graph, 'timestamp')
    timestamp.text = datetime.datetime.now().strftime("%c")

    # Add options
    options_data = data.get('options', {})
    options_block = SubElement(flow_graph, 'block')
    options_key = SubElement(options_block, 'key')
    options_key.text = 'options'

    param = SubElement(options_block, 'param')
    param_key = SubElement(param, 'key')
    param_key.text = 'id'
    param_value = SubElement(param, 'value')
    param_value.text = options_data.get('id')

    for key, value in options_data.get('parameters', {}).items():
        param = SubElement(options_block, 'param')
        param_key = SubElement(param, 'key')
        param_key.text = key
        param_value = SubElement(param, 'value')
        param_value.text = str(value)

    # Add blocks
    for block_data in data.get('blocks', []):
        block = SubElement(flow_graph, 'block')
        block_key = SubElement(block, 'key')
        block_key.text = block_data['key']

        param = SubElement(block, 'param')
        param_key = SubElement(param, 'key')
        param_key.text = 'id'
        param_value = SubElement(param, 'value')
        param_value.text = block_data['id']

        for key, value in block_data.get('parameters', {}).items():
            param = SubElement(block, 'param')
            param_key = SubElement(param, 'key')
            param_key.text = key
            param_value = SubElement(param, 'value')
            param_value.text = str(value)

        for key, value in block_data.get('states', {}).items():
            param = SubElement(block, 'param')
            param_key = SubElement(param, 'key')
            param_key.text = f'_{key}' # States are prefixed with _
            param_value = SubElement(param, 'value')
            param_value.text = str(value)

    # Add connections
    for connection_data in data.get('connections', []):
        connection = SubElement(flow_graph, 'connection')

        source_block_id = SubElement(connection, 'source_block_id')
        source_block_id.text = connection_data[0]

        sink_block_id = SubElement(connection, 'sink_block_id')
        sink_block_id.text = connection_data[2]

        source_key = SubElement(connection, 'source_key')
        source_key.text = str(connection_data[1])

        sink_key = SubElement(connection, 'sink_key')
        sink_key.text = str(connection_data[3])

    # Write the XML to the output file
    xml_str = tostring(flow_graph, 'utf-8')
    pretty_xml_str = minidom.parseString(xml_str).toprettyxml(indent="  ")
    with open(grc_file, 'w') as f:
        f.write(pretty_xml_str)

    print(f"Successfully converted {yaml_file} to {grc_file}")

def convert_grc_to_yaml(grc_file, yaml_file):
    """
    Converts a GRC XML file to a GRC YAML file.
    """
    try:
        tree = parse(grc_file)
    except FileNotFoundError:
        print(f"Error: Input file not found at {grc_file}")
        return
    except Exception as e:
        print(f"Error parsing GRC file: {e}")
        return

    root = tree.getroot()

    data = {
        'options': {},
        'blocks': [],
        'connections': []
    }

    for block in root.findall('block'):
        key = block.find('key').text
        if key == 'options':
            options_data = {'parameters': {}}
            for param in block.findall('param'):
                param_key = param.find('key').text
                param_value = param.find('value').text
                if param_key == 'id':
                    options_data['id'] = param_value
                else:
                    options_data['parameters'][param_key] = param_value
            data['options'] = options_data
        else:
            block_data = {
                'key': key,
                'id': '',
                'parameters': {},
                'states': {}
            }
            for param in block.findall('param'):
                param_key = param.find('key').text
                param_value = param.find('value').text
                if param_key == 'id':
                    block_data['id'] = param_value
                elif param_key.startswith('_'):
                    block_data['states'][param_key[1:]] = param_value
                else:
                    block_data['parameters'][param_key] = param_value
            data['blocks'].append(block_data)

    for connection in root.findall('connection'):
        source_id = connection.find('source_block_id').text
        sink_id = connection.find('sink_block_id').text
        source_key = connection.find('source_key').text
        sink_key = connection.find('sink_key').text
        data['connections'].append([source_id, source_key, sink_id, sink_key])

    with open(yaml_file, 'w') as f:
        yaml.dump(data, f, default_flow_style=False)

    print(f"Successfully converted {grc_file} to {yaml_file}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Convert GRC files between YAML and XML formats.')
    parser.add_argument('input_file', help='The input file.')
    parser.add_argument('output_file', help='The output file.')
    parser.add_argument('--to_grc', action='store_true', help='Convert from YAML to GRC XML.')
    parser.add_argument('--to_yaml', action='store_true', help='Convert from GRC XML to YAML.')
    args = parser.parse_args()

    if args.to_grc:
        convert_yaml_to_grc(args.input_file, args.output_file)
    elif args.to_yaml:
        convert_grc_to_yaml(args.input_file, args.output_file)
    else:
        print("Please specify a conversion direction with --to_grc or --to_yaml.")
