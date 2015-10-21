#! /usr/bin/env python
# -*- coding: utf-8 -*-
####################
# Indigo Copyright (c) 2013, Perceptive Automation, LLC. All rights reserved.
# http://www.indigodomotics.com
#
# Requires Mosquitto MQTT (v3.1) Client v1.4 to be installed on a pre-configured Broker Server
# Visit http://simplifiedthinking.co.uk/2015/10/07/install-mqtt-server/ for instructions
#
# CHANGE LOG
#
# Version    |  Description
# -------------------------
# 1.0.0         Initial Release
#
# 1.0.1         Fixed Bug - removed redundant process terminate in shutdown method
#               In Development ...
#

import indigo

import os
import sys
import signal
import Queue
import threading
import subprocess

# Note the "indigo" module is automatically imported and made available inside
# our global name space by the host process.


class Plugin(indigo.PluginBase):
    ########################################
    # Main Functions
    ######################

    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)

        self.debug = pluginPrefs.get("mqttDebugInfo", False)

        try:
            self.sleepinterval = int(pluginPrefs.get("mqttSleepPoll", 30))
        except:
            self.sleepinterval = 30
        
        if self.debug == True:
            self.debugLog("mqtt debugging enabled")

    def __del__(self):
        indigo.PluginBase.__del__(self)


    def startup(self):
        self.debugLog("startup called")

        # set the global variables
        self.mqttProc = []
        self.mqttPid = { "topic" : 0 }
        self.mqttProc_id = 0
        self.io_q = Queue.Queue()
    
        # start the queue reader
        threading.Thread(target = self.io_queue_reader).start()
 

    def shutdown(self):
        self.debugLog("shutdown called")


    def runConcurrentThread(self):
        try:
            self.debugLog("Starting ConcurrentThread ... Active threads = " + str(threading.activeCount()))

            while True:
                for dev in indigo.devices.iter("self"):
                    if not dev.enabled or not dev.configured:
                        continue
            
                    # check to see if the listener is still running ...
                    checkPid = self.mqttPid[dev.pluginProps["brokerName"] + dev.pluginProps["brokerTopic"]]
            
                    try:
                        os.kill(checkPid, 0)
                    except OSError:
                        # the process has stopped, restart it
                        self.deviceStartComm(dev)

                self.sleep(self.sleepinterval)

        except self.StopThread:
            pass	# Optionally catch the StopThread exception and do any needed cleanup.


    def deviceStartComm(self, dev):
        # start the mqtt listener thread for this device, storing the PID for future management
        if dev.enabled and dev.configured:
            self.mqttProc.append(subprocess.Popen(['/usr/local/bin/mosquitto_sub', '-h', dev.pluginProps["brokerName"], '-t', dev.pluginProps["brokerTopic"]], stdout=subprocess.PIPE))
            threading.Thread(target = self.mqtt_listener, name = dev.name.replace(" ",""), args = (self.mqttProc[self.mqttProc_id], dev.pluginProps["brokerName"], dev.pluginProps["brokerTopic"])).start()
 
            self.mqttPid[dev.pluginProps["brokerName"] + dev.pluginProps["brokerTopic"]] = self.mqttProc[self.mqttProc_id].pid
            self.mqttProc_id = self.mqttProc_id + 1


    def deviceStopComm(self, dev):
        # stop the mqtt listener thread for this device
        if dev.enabled and dev.configured:
            targetPid = self.mqttPid[dev.pluginProps["brokerName"] + dev.pluginProps["brokerTopic"]]
            
            try:
                os.kill(targetPid, signal.SIGKILL)
                self.debugLog("stopped mqtt_listener for pid: " + str(targetPid))

            except:
                failed = 1 # does nothing


    ########################################
    # Sensor Functions
    ######################

    def mqtt_listener(self, proc, broker, topic):
        self.debugLog("mqtt_listener for " + broker + ":" + topic + " started with pid: " + str(proc.pid))
        
        while True:
            line = proc.stdout.readline()
            
            if line != '':
                self.io_q.put([broker, topic, line.rstrip()])
            
            if proc.poll() != None:
                self.debugLog("mqtt_listener for " + broker + ":" + topic + " has stopped")
                break


    def io_queue_reader(self):
        self.debugLog("io_queue_reader started")
        
        onOffState = { "ON" : 1, "OFF" : 0 }
        
        while True:
            try:
                broker, topic, item = self.io_q.get(True, 1)
            except Queue.Empty:
                empty = 1
            else:
                for dev in indigo.devices.iter("self"):
                    if [dev.pluginProps["brokerName"] + dev.pluginProps["brokerTopic"]] == [broker + topic]:
                        self.debugLog("io_queue_reader:" + broker + ":" + topic + ": " + item)
                        indigo.server.log("%s received mqtt message from %s" % (dev.name, topic))

                        if item.upper() in ("ON", "OFF"):
                            dev.updateStateOnServer("onOffState", value=onOffState[item.upper()])
                        else:
                            dev.updateStateOnServer("topicMessage", value=item)
    

    ########################################
    # Custom Action Callbacks
    ######################

    def sendMessage(self, action, dev):
        brokerName = dev.pluginProps["brokerName"]
        brokerTopic = dev.pluginProps["brokerTopic"]

        try:
            brokerMessage = action.props.get("brokerMessage")
        except:
            # something wrong in the text typed by the user
            self.debugLog("unable to correctly assign message for sending")
            return False

        self.debugLog("sending message to " + brokerName + ":" + brokerTopic + " with content " + brokerMessage)
        p = subprocess.Popen(['/usr/local/bin/mosquitto_pub', '-h', brokerName, '-t', brokerTopic, '-m', brokerMessage], stdout=subprocess.PIPE)
        p.wait()
            
        if p.returncode != 0:
            self.debugLog("could not send message; failed with error code " + str(p.returncode))
            return False
    
        indigo.server.log(u"sent \"%s\" %s" % (dev.name, "message"))


    ########################################
    # Inherited Action Callbacks
    ######################

    def actionControlGeneral(self, action, dev):
        if action.deviceAction == indigo.kDeviceGeneralAction.RequestStatus:
            # publish a status request message to the mqtt broker
            
            brokerName = dev.pluginProps["brokerName"]
            brokerTopic = dev.pluginProps["brokerStatusTopic"]
            brokerMessage = dev.pluginProps["brokerStatusMessage"]
            
            self.debugLog("sending status request to " + brokerName + ":" + brokerTopic + " with message " + brokerMessage)
            p = subprocess.Popen(['/usr/local/bin/mosquitto_pub', '-h', brokerName, '-t', brokerTopic, '-m', brokerMessage], stdout=subprocess.PIPE)
            p.wait()
            
            if p.returncode != 0:
                self.debugLog("could not send status request; failed with error code " + str(p.returncode))
                return False
            
            indigo.server.log(u"sent \"%s\" %s" % (dev.name, "status request"))


    ########################################
    # Device Configuration callbacks
    ######################
    def validateDeviceConfigUi(self, valuesDict, typeId, devId):
        self.debugLog("validating configUi")

        brokerName = valuesDict["brokerName"]
        brokerTopic = valuesDict["brokerTopic"]

        p = subprocess.Popen(['/usr/local/bin/mosquitto_pub', '-h', brokerName, '-t', brokerTopic, '-m', "test message"], stdout=subprocess.PIPE)
        p.wait()
        
        if p.returncode != 0:
            self.debugLog("Could not connect to the MQTT broker running at " + brokerName + ". Error code: " + str(p.returncode))
            e = indigo.Dict()
            e["brokerName"] = 1
            e["showAlertText"] = "Could not connect to the MQTT broker running at " + brokerName
            return (False, valuesDict, e)

        return (True, valuesDict)


# To Be Implemented
#
# Error Handling - How does the sub client behave when it loses connection to the server for network reasons.  Multiple tries, final disable of device?
# Startup State - If no update is broadcast from the listening device, should the state be set to off or another default value specific by the user?
# Process Check - regularly check that listener processes are still running and restart if not


