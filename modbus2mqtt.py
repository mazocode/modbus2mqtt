#!/usr/bin/python3.11

import sys
import getopt
import logging
import signal
import time
import yaml
import json
import re
from typing import List
from queue import Queue
from threading import Thread, Lock
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException
from pymodbus.pdu import ExceptionResponse
# from pymodbus.constants import Endian
# from pymodbus.payload import BinaryPayloadDecoder
from pymodbus.transaction import ModbusSocketFramer
from paho.mqtt import client as mqtt_client

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("modbus2mqtt")

sigStop = False
config = {}
sources = []
schema = {}


class MqttBroker:

    def __init__(self, host: str, port: int, username: str, password: str,
                topic_prefix: str, tls: bool = False, clientid: str = "modbus2mqtt"):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.topic_prefix = topic_prefix
        self.tls = tls
        self.clientid = clientid
        self.client = mqtt_client.Client(self.clientid)
        self.client.username_pw_set(self.username, self.password)
        self.is_connected = False
        self.subscribers = {}
        if self.tls is True:
            # sslcontext = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
            # sslcontext.check_hostname = False
            # self.client.tls_set(cert_reqs=ssl.CERT_NONE, keyfile=None, certfile=None)
            self.client.tls_set(keyfile=None, certfile=None)
            self.client.tls_insecure_set(True)
        log.info("Connecting to MQTT broker %s at port %d", self.host, self.port)
        self.client.on_connect = self.on_connect
        self.client.on_publish = self.on_publish
        self.client.on_message = self.on_message
        self.client.connect(self.host, port=self.port, keepalive=60)
        self.client.loop_start()

    def on_publish(self, client, userdata, result):
        log.debug("Data published")

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            log.info("Connected dto broker %s", self.host)
            self.is_connected = True
        else:
            log.error("Connection to broker %s failed.", self.host)
            log.error(userdata)
            log.error(flags)

    def on_message(self, client, userdata, message):
        subscriber = self.subscribers.get(message.topic.lower(), None)
        if subscriber is not None:
            data = None
            try:
                data = json.loads(str(message.payload.decode("utf-8")))
            except Exception as e:
                log.error(f"Invalid message received via topic {message.topic} (retained={message.retain}): {e}")
                return
            subscriber.enqueue(data)
        return 0

    def publish(self, topic: str, value: str, retain: bool = True):
        if self.is_connected is not True:
            return False
        t = topic
        if self.topic_prefix is not None:
            t = self.topic_prefix + '/' + t
        log.debug("Publishing topic %s value %s", t, value)
        ret = self.client.publish(t, value, retain=retain)
        if ret[0] != 0:
            log.error("Failed to deliver %s", t)
            return False
        return True

    def rpc_subscribe(self, src):
        t = src.topic_prefix
        if self.topic_prefix is not None:
            t = self.topic_prefix + '/' + t
        self.subscribers[t.lower() + '/rpc'] = src
        log.info(f"Subscribing to rpc topic {t}/rpc.")
        self.client.subscribe(t + '/rpc', 0)

    def rpc_unsubscribe(self, src):
        t = src.topic_prefix
        if self.topic_prefix is not None:
            t = self.topic_prefix + '/' + t
        if self.subscribers.get(t.lower() + '/rpc', None) is not None:
            log.info(f"Unsubscribing from rpc topic {t}/rpc.")
            self.client.unsubscribe(t + '/rpc')
            del self.subscribers[t.lower() + '/rpc']


class Register:

    def __init__(self, name: str, topic: str, register: int, length: int, mode: str, unitid: int = None):
        self.name = name
        self.topic = topic
        self.start = register
        self.length = length
        self.mode = [*mode]
        self.unitid = unitid

    def get_value(self, src):
        log.debug("Method not implemented.")
        return False

    def set_value(self, params):
        log.debug("Method not implemented.")
        return False

    def can_read(self):
        return 'r' in self.mode

    def can_write(self):
        return 'w' in self.mode


class CoilsRegister(Register):

    def __init__(self, name: str, topic: str, register: int, coils: list,
                length: int = 1, mode: str = "rw", unitid: int = None, **kvargs):
        super().__init__(name, topic, register, length, mode, unitid=unitid)
        self.length = length
        self.coils = []
        bit = 0
        for c in coils:
            bit += 1
            if c.get('bit', None) is not None:
                bit = c.get('bit', 1)
            if bit > self.length:
                self.length = bit
            c["bit"] = bit
            if 'on_value' not in c:
                c["on_value"] = "ON"
            if 'off_value' not in c:
                c["off_value"] = "OFF"
            if 'mode' not in c:
                c["mode"] = self.mode
            else:
                c["mode"] = [*c["mode"]]
            if 'name' not in c:
                c["name"] = f"coil_{bit}"
            self.coils.append(c)

    def get_value(self, src):
        unitid = self.unitid
        if unitid is None:
            unitid = src.unitid

        rr = src.client.read_coils(self.start, self.length, slave=unitid)
        if not rr:
            raise ModbusException("Received empty modbus respone.")
        if rr.isError():
            raise ModbusException(f"Received Modbus library error({rr}).")
        if isinstance(rr, ExceptionResponse):
            raise ModbusException(f"Received Modbus library exception ({rr}).")

        val = {}
        for c in self.coils:
            name = re.sub(r'/\s\s+/g', '_', str(c["name"]).strip())
            if rr.bits[c["bit"] - 1] == 0:
                val[name] = c["off_value"]
            else:
                val[name] = c["on_value"]
        return val

    def set_value(self, src, params):
        unitid = self.unitid
        if unitid is None:
            unitid = src.unitid

        value = params.get("value", None)
        if value is None:
            # Can't set unknown state
            return False

        cname = params.get("coil", None)
        if cname is None:
            # Can't set unknown state
            return False

        # Find the coil to set
        coil = None
        for c in self.coils:
            name = re.sub(r'/\s\s+/g', '_', str(c["name"]).strip())
            if cname == name or cname == str(c["name"]):
                if 'w' not in c["mode"]:
                    # Can't write to this coil
                    log.info("Could not write becaue coil mode is set to read-only.")
                    return False
                coil = c
                break

        if coil is None:
            return False

        if value == c["on_value"] or (not isinstance(value, str) and bool(value) is True):
            value = True
        else:
            value = False

        addr = self.start + int(coil["bit"]) - 1
        log.debug(f"Writing coil at address {addr} with value {value}.")
        rr = src.client.write_coil(addr, value, slave=unitid)
        if not rr:
            raise ModbusException("Received empty modbus respone.")
        if rr.isError():
            raise ModbusException(f"Received Modbus library error({rr}).")
        if isinstance(rr, ExceptionResponse):
            raise ModbusException(f"Received Modbus library exception ({rr}).")


class HoldingRegister(Register):

    def __init__(self, name: str, topic: str, register: int, length: int = 2,
                mode: str = "r", substract: float = 0, divide: float = 1,
                decimals: int = 0, signed: bool = False, unitid: int = None, **kvargs):
        super().__init__(name, topic, register, length, mode, unitid=unitid)
        self.divide = divide
        self.decimals = decimals
        self.substract = substract
        self.signed = signed

    def get_value(self, src):
        unitid = self.unitid
        if unitid is None:
            unitid = src.unitid

        rr = src.client.read_holding_registers(self.start, self.length, slave=unitid)
        if not rr:
            raise ModbusException("Received empty modbus respone.")
        if rr.isError():
            raise ModbusException(f"Received Modbus library error({rr}).")
        if isinstance(rr, ExceptionResponse):
            raise ModbusException(f"Received Modbus library exception ({rr}).")

        val = rr.registers[0]
        if self.signed and int(val) >= 32768:
            val = int(val) - 65535
        if self.decimals > 0:
            fmt = '{0:.' + str(self.decimals) + 'f}'
            val = float(fmt.format((int(val) - float(self.substract)) / float(self.divide)))
        else:
            val = int(((int(val) - float(self.substract)) / float(self.divide)))
        return val


class Schema:

    def __init__(self, name: str, readings: List[Register]):
        self.name = name
        self.readings = readings


class ModbusSource:

    def __init__(self, name: str, broker: MqttBroker, host: str, port: int,
                schema: Schema, unitid: int = 1, topic_prefix: str = None,
                pollms: int = 100, enabled: bool = True):
        self.mqtt = broker
        self.host = host
        self.port = port
        self.unitid = unitid
        self.schema = schema
        self.name = name
        self.enabled = enabled
        self.queue = Queue()
        if enabled:
            self.client = ModbusTcpClient(host=self.host,
                            port=self.port,
                            retries=1,
                            timeout=10,
                            retry_on_empty=True,
                            framer=ModbusSocketFramer,
                            close_comm_on_error=False)
        self.cache = {}
        self.track = {}
        self.lock = Lock()
        self.is_active = False
        self.pollms = pollms
        self.is_online = False
        self.was_online = None
        if topic_prefix:
            self.topic_prefix = topic_prefix
        else:
            self.topic_prefix = re.sub(r'/\s\s+/g', '_', self.name.strip().lower())

        self.mqtt.rpc_subscribe(self)

    def enqueue(self, action: dict):
        self.queue.put(action)

    def poller_thread(self):
        log.info("Connecting to modbus server %s on port %d", self.host, self.port)
        self.client.connect()
        self.is_active = True
        try:
            while sigStop is False:
                for r in self.schema.readings:

                    if sigStop:
                        break

                    if r.can_read():

                        log.debug("Reading %s (register %d with length %d from unit %d)",
                                r.name, r.start, r.length, self.unitid)
                        val = None

                        try:
                            val = r.get_value(self)
                        except ModbusException as e:
                            log.error(f"Received exception({e}) while trying to read from modbus slave.")
                            self.is_online = False
                            continue

                        self.is_online = True
                        rid = id(r)
                        with self.lock:
                            if self.cache.get(rid, None) is None or self.cache[rid] != val:
                                self.track[rid] = True
                            self.cache[rid] = val

                while not self.queue.empty():

                    if sigStop:
                        break

                    msg = self.queue.get()
                    if not msg:
                        continue

                    name = msg.get("target", None)
                    if name is None:
                        continue

                    # Find the target
                    target = None
                    for r in self.schema.readings:
                        if r.topic == name or r.name == name:
                            target = r
                            break

                    if target is None:
                        log.info(f"Could not find rpc message target {name}")
                        continue

                    match msg.get("method", "invalid"):

                        case "set":
                            target.set_value(self, msg.get("params", {}))

                time.sleep(self.pollms / 1000)
        finally:
            try:
                self.client.close()
                topic = self.topic_prefix + '/online'
                if self.mqtt.is_connected:
                    self.mqtt.publish(topic, str(False).lower())
                self.mqtt.rpc_unsubscribe(self)
            finally:
                self.is_active = False

    def publish_changes(self):
        if self.mqtt.is_connected is not True:
            return

        if self.was_online is None or self.was_online != self.is_online:
            topic = self.topic_prefix + '/online'
            if self.mqtt.publish(topic, str(True).lower()):
                self.was_online = self.is_online

        for r in self.schema.readings:
            with self.lock:
                rid = id(r)
                if not self.track.get(rid, False):
                    continue
                topic = self.topic_prefix + '/' + r.topic
                val = None
                if isinstance(self.cache[rid], dict):
                    val = json.dumps(self.cache[rid])
                else:
                    val = str(self.cache[rid])
                if self.mqtt.publish(topic, val):
                    self.track[rid] = False
                else:
                    if self.mqtt.is_connected is not True:
                        return


def on_stop_signal(signum, frame):
    global sigStop
    sigStop = True
    log.debug("Received stop signal.")


def main(argv):
    global sigStop, sources, log
    signal.signal(signal.SIGINT, on_stop_signal)
    signal.signal(signal.SIGTERM, on_stop_signal)

    cfgfile = "config.yaml"
    opts, args = getopt.getopt(argv, "hc:", ["config="])
    for opt, arg in opts:
        if opt in ("-c", "--config"):
            cfgfile = arg

    with open(cfgfile, 'r') as file:
        config = yaml.safe_load(file)

    for s in config["schema"]:
        regs = []
        for r in s["readings"]:
            if r.get("coils", None) is None:
                regs.append(HoldingRegister(**r))
            else:
                regs.append(CoilsRegister(**r))
        schema[s["name"]] = Schema(s["name"], regs)

    log.debug("Configuring mqtt broker %s", config["mqtt"]["host"])
    broker = MqttBroker(
        config["mqtt"].get("host", "localhost"),
        int(config["mqtt"].get("port", 1883)),
        config["mqtt"].get("username", None),
        config["mqtt"].get("password", None),
        config["mqtt"].get("topic_prefix", None),
        bool(config["mqtt"].get("tls", False)),
        clientid=str(config["mqtt"].get("clientid", "modbus2mqtt")))

    for source in config["sources"]:
        log.debug("Configuring source %s", source["name"])
        sources.append(
            ModbusSource(
                source["name"],
                broker,
                source.get("host", "localhost"),
                int(source.get("port", 502)),
                schema[source["schema"]],
                int(source.get("unitid", 1)),
                topic_prefix=source.get("topic_prefix", None),
                enabled=bool(source.get("enabled", True))
            )
        )

    log.debug("Init complete.")

    for i in sources:
        if i.enabled:
            log.info("Staring poller for %s", i.host)
            t = Thread(target=i.poller_thread)
            t.daemon = True
            t.start()

    while sigStop is False:

        time.sleep(1)

        if broker.is_connected is not True:
            continue

        for i in sources:
            i.publish_changes()

    for i in sources:
        log.info("Waiting for poller %s", i.host)
        while i.is_active:
            time.sleep(1)


if __name__ == "__main__":
    main(sys.argv[1:])
