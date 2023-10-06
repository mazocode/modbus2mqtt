#!/usr/bin/python3.11

import sys, getopt
import socket
import logging
#import libscrc
import signal
import time
import yaml
import ssl
from typing import List
from threading import Thread, Lock
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException
from pymodbus.pdu import ExceptionResponse
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder
from pymodbus.transaction import (
    ModbusAsciiFramer,
    ModbusBinaryFramer,
    ModbusRtuFramer,
    ModbusSocketFramer,
    ModbusTlsFramer,
)
from paho.mqtt import client as mqtt_client

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("modbus2mqtt")

sigStop = False
config = {}
sources = []
schema = {}

class MqttBroker:
    def __init__(self, host : str, port : int, username : str, password: str, topic_prefix : str, tls : bool = False):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.topic_prefix = topic_prefix
        self.tls = tls
        self.client_id = "modbus2mqtt"
        self.client = mqtt_client.Client(self.client_id)
        self.client.username_pw_set(self.username, self.password)
        self.is_connected = False
        if self.tls == True:
            #sslcontext = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
            #sslcontext.check_hostname = False
            #self.client.tls_set(cert_reqs=ssl.CERT_NONE, keyfile=None, certfile=None)
            self.client.tls_set(keyfile=None, certfile=None)
            self.client.tls_insecure_set(True)
        log.info("Connecting to MQTT broker %s at port %d", self.host, self.port)
        self.client.on_connect = self.on_connect
        self.client.on_publish = self.on_publish
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

class Register:
    def __init__(self, name : str, topic: str, start : int, length : int = 2, substract : float = 0, divide : float = 1, decimals : int = 0, signed : bool = False):
        self.name = name
        self.topic = topic
        self.start = start
        self.length = length
        self.divide = divide
        self.decimals = decimals
        self.substract = substract
        self.signed = signed

class Schema:
    def __init__(self, name : str, readings : List[Register]):
        self.name = name
        self.readings = readings

class ModbusSource:
    def __init__(self, name : str, broker: MqttBroker, host : str, port : int, schema : Schema, unitid : int = 1, topic_prefix : str = None):
        self.mqtt = broker
        self.host = host
        self.port = port
        self.unitid = unitid
        self.schema = schema
        self.name = name
        self.client = ModbusTcpClient(host=self.host, port=self.port, retries=1, timeout=10, retry_on_empty=True, framer=ModbusSocketFramer, close_comm_on_error=False)
        self.cache = {}
        self.track = {}
        self.lock = Lock()
        self.active = False
        if topic_prefix:
            self.topic_prefix = topic_prefix
        else:
            self.topic_prefix = self.name

    def poller_thread(self):
        log.info("Connecting to modbus server %s on port %d", self.host, self.port)
        self.client.connect()
        self.active = True
        try:
            while sigStop == False:
                for r in self.schema.readings:

                    if sigStop:
                        break

                    log.debug("Reading %s (register %d with length %d from unit %d)", r.name, r.start, r.length, self.unitid)
                    try:
                        rr = self.client.read_holding_registers(r.start, r.length, slave=self.unitid)
                    except ModbusException as e:
                        log.error(f"Received ModbusException({ec}) from library")
                        continue
                    if rr.isError():
                        log.error(f"Received Modbus library error({rr})")
                        continue
                    if isinstance(rr, ExceptionResponse):
                        log.error(f"Received Modbus library exception ({rr})")
                        continue
                    #decoder = BinaryPayloadDecoder.fromRegisters(
                    #    rr.registers,
                    #    byteorder=Endian.BIG,
                    #    wordorder=Endian.BIG
                    #)
                    val = rr.registers[0]
                    if r.signed and int(val) >= 32768:
                        val = int(val)-65535
                    if r.decimals > 0:
                        fmt = '{0:.'+str(r.decimals)+'f}'
                        val = float(fmt.format((int(val)-float(r.substract)) / float(r.divide)))
                    else:
                        val = int(((int(val)-float(r.substract)) / float(r.divide)))

                    log.debug("got %s", val)

                    with self.lock:
                        if self.cache.get(r.start, None) == None or self.cache[r.start] != val:
                            self.track[r.start] = True
                        self.cache[r.start] = val
                
                time.sleep(1)
        finally:
            try:
                self.client.close()
            finally:
                self.active = False

def on_stop_signal(signum, frame):
    global sigStop
    sigStop = True
    log.debug("Received stop signal.")

def main(argv):
    global sigStop, sources, log
    signal.signal(signal.SIGINT, on_stop_signal)
    signal.signal(signal.SIGTERM, on_stop_signal)

    cfgfile = "config.yaml"
    opts, args = getopt.getopt(argv,"hc:",["config="])
    for opt, arg in opts:
        if opt in ("-c", "--config"):
            cfgfile = arg

    with open(cfgfile, 'r') as file:
        config = yaml.safe_load(file)

    for s in config["schema"]:
        regs = []
        for r in s["readings"]:
            regs.append(
                Register(
                    r["name"], 
        	    r["topic"], 
                    int(r["register"]), 
                    int(r.get("length", 2)), 
                    float(r.get("substract", 0)), 
                    float(r.get("divide", 1)), 
                    int(r.get("decimals", 0)),
                    bool(r.get("signed", False))
                )
            )
        schema[s["name"]] = Schema(s["name"], regs)

    log.debug("Configuring mqtt broker %s", config["mqtt"]["host"])
    b = MqttBroker(
            config["mqtt"]["host"], 
            int(config["mqtt"]["port"]), 
            config["mqtt"]["username"], 
            config["mqtt"]["password"], 
            config["mqtt"]["topic_prefix"], 
            bool(config["mqtt"]["tls"])
        )

    for source in config["sources"]:
        log.debug("Configuring source %s", source["name"])
        sources.append(
            ModbusSource(
                source["name"], 
                b, 
                source["host"], 
                int(source["port"]), 
                schema[source["schema"]], 
                int(source.get("unitid", 1)), 
                topic_prefix = source.get("topic_prefix", None)
            )
        )

    log.debug("Init complete.")

    for i in sources:
        log.info("Staring poller for %s", i.host)
        t = Thread(target=i.poller_thread)
        t.daemon = True
        t.start()

    while sigStop == False:

        time.sleep(3)

        for i in sources:
            if i.mqtt.is_connected != True:
                continue
            topic_prefix = i.mqtt.topic_prefix + '/' + i.topic_prefix
            for r in i.schema.readings:
                topic = topic_prefix + '/' + r.topic
                with i.lock:
                    changed = i.track.get(r.start, None)
                    if changed == True:
                        log.debug("Publishing topic %s value %s", topic, str(i.cache[r.start]))
                        ret = i.mqtt.client.publish(topic, str(i.cache[r.start]))
                        if ret[0] != 0:
                            log.error("Failed to deliver %s", topic)
                        else:
                            i.track[r.start] = False

    for i in sources:
        log.info("Waiting for poller %s", i.host)
        while i.active:
    	    time.sleep(1)

if __name__ == "__main__":
    main(sys.argv[1:])
