# Modbus <-> MQTT Gateway

![code-analysis](https://github.com/mazocode/modbus2mqtt/actions/workflows/run-code-analysis.yaml/badge.svg)

This is a gateway between Modbus and MQTT. It was originally developed to read registers from PV inverters via Modbus and publish them as MQTT message. The gateway 
can read from one or more Modbus TCP gateways (e.g. the Waveshare RS485 to Ethernet) simultaneously. The state of the coils can be published as a json message and their 
state can also be changed with a json message sent to the rpc topic.

## Changes from the upstream version
1) Read input register
2) Basic support of big and little endian registers
3) Read multi-byte value in big and little endian order
4) Fix the default value of length to 1 ( as in the readme)

## Configuration

Create a configuration file (see examples/). Here is the basic syntax:

```yaml
mqtt:

  # Broker connection parameters

  host: "<mqtt-host>"    # <--- (optional) MQTT broker host (default is localhost)
  port: 1883             # <--- (optional) MQTT broker port (default is 1883)
  tls: false             # <--- (optional) Use TLS for broker connection (default is False)
  username: "<username>" # <--- (required) Client username
  password: "<password>" # <--- (required) Client password
  clientid: "myclient"   # <--- (optional) The client id (default is modbus2mqtt)
  topic_prefix: "deye"   # <--- (optional) Path that will prepend all topics before publication, if set


schema:

  # You can publish data from different devices in parallel. Create
  # a schema for each device type with the registers to publish:

  - name: "deye-inverter"   # <--- Unique schema name
    readings:

    # Holdings

    - name: "System State"  # <--- (required) Friendly name for the data point
      topic: "system/state" # <--- (required) Appened after <mqtt.topic_prefix>/<source.topic_prefix>/
      register: 500         # <--- (required) Holding register to read from
      unitid: 1             # <--- (optional) Overwrites source unitid
      length: 1             # <--- (optional) Length of the data in byte (default is 1)
      substract: 0          # <--- (optional) Value to substract before publishing (default is 0)
      signed: 1             # <--- (optional) True/false for signed/unsigned value (default is 0)
      divide: 1             # <--- (optional) Value to divide by before publishing (default is 1)
      typereg: holding      # <--- (optional) Read holding or input register (default is holding)
      littleendian: 0       # <--- (optional) Byte order (default is 0)

   # Coils
 
   - name: "Digital Outputs 1"
     topic: "outputs"
     unitid: 2
     register: 0
     coils:
     - name: "switch1"       # <--- (required) Property to use in the json message sent to the topic
       bit: 1                # <--- (optional) bit (coil) to read from (default is 1)
       on_value: 1           # <--- (optional) Json value if bit is set (default is "ON")
       off_value: 0          # <--- (optional) Json value is bit is unset (default is "OFF")
       mode: r               # <--- (optional) Coil is read-only if set to r. (default: rw)
     - name: "switch3"
       bit: 3
       on_value: True
       off_value: False

    # ...

sources:

  # Your modbus sources to retrieve data from

  - name: "deye-inverter"  # <--- (required) Name of your soruce
    schema: deye-inverter  # <--- (required) Name of the schema to read from this device
    host: "192.168.0.30"   # <--- (optional) Replace with the modbus gateway IP (default is loclahost)
    port: 502              # <--- (optional) Port your gateway is listening on (default is 502)
    unitid: 1              # <--- (optional) Modbus device id to read from (default is 1)
    topic_prefix: "abc"    # <--- (optional) Appened after <mqtt.topic_prefix>/
    pollms: 1000           # <--- (optional) Modbus polling interval in ms (default is 1000)
```


## Running

### Docker

Mount the configuration file to /config.yaml:

```bash
docker run --rm \
	--net host \
	--volume ./config.yaml:/config.yaml:ro \
	ghcr.io/mazocode/modbus2mqtt
```

### Systemd

Adjust the service definition from systemd/ with the path you have clone the repository into and the path to your configuration file.

### Manual 

```bash
modbus2mqtt.py -c /path/to/config.yaml
```

## RPC Topic

The gateway will listen to commands on the /rpc topic prefix of each modbus source. Example:
```yaml
mqtt:
  topic_prefix: "root"

schema:
- name: neuron
  readings:
  - name: "Digital Outputs 1"
    topic: "outputs"
    unitid: 1
    register: 0
    coils:
    - name: "switch1"
      bit: 1
      on_value: "Yes"
      off_value: "No"

sources:
  - name: "mydevice"
    schema: neuron
    topic_prefix: "b"
  - name: "mydevice"
    schema: neuron
    topic_prefix: "c"
```

This configuration will listen to topic /root/b/rpc and topic /root/c/rpc for commands to the second modbus device. For now there is only a single command
implemented for setting the stat eof a single coil. The general syntax of the json message you can send to the rpc topic is:

```json
{ 
  "method": "set", 
  "target": "outputs", 
  "params": { 
    "value": false, 
    "coil": "switch1" 
  } 
}
```

The value can be any boolean representation understood by python or a value specified with on_value or off_value within the schema definition.


## Examples

### Home Assistant with Deye SUN-xK-SG04LP3-EU inverters

See examples/Deye-SG04LP3-EU/

You can use home-assistant.yaml as a template for the MQTT integration. This example assumes messages are published to the following topic syntax: pv/plant-id/inverter-id:

```yaml
mqtt:
  topic_prefix: "pv"

schema:
  - name: "deye-sg04lp3-eu"
    readings:
    - name: "System State"
      topic: "system/state" # <--- becomes pv/i<1,2,3>/system/state
      # ...

sources:
  - name: "inverter1"
    schema: deye-sg04lp3-eu
    topic_prefix: "1/i1"    # <--- becomes pv/1/i1
  - name: "inverter2"
    schema: deye-sg04lp3-eu
    topic_prefix: "1/i2"    # <--- becomes pv/1/i2
    # ...
```

Result:

![MQTT Explorer Screenshot](examples/Deye-SG04LP3-EU/deye-modbus2mqtt-example.jpg)

Home Assistant device sensors:

![MQTT Explorer Screenshot](examples/Deye-SG04LP3-EU/ha-mqtt-deye-data.jpg)


## Known Issues

- SSL with self signed certificates seems broken

## Contributing

I am always happy to receive contributions. If you plan to contribute back to this repo, please fork & open a PR.
