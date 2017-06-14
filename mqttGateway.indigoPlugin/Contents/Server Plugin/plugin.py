#! /usr/bin/env python
# -*- coding: utf-8 -*-
####################
# Indigo Copyright (c) 2013, Perceptive Automation, LLC. All rights reserved.
# http://www.indigodomotics.com
#
# Requires Mosquitto MQTT (v3.1) Client v1.4 to be installed on a pre-configured Broker Server
# Visit http://simplifiedthinking.co.uk/2015/10/07/install-mqtt-server/ for instructions
#
# mqttGateway v1.1.0 Copyright (c) 2017, Simplified Thinking / Jeremy Rutherford.
#
# CHANGE LOG
#
# Version    |  Description
# -------------------------
# 1.0.0         Initial Release
#
# 1.0.1         Fixed Bug - removed redundant process terminate in shutdown method
#
# 1.0.2         Changed method used for creating new sub processes, limiting number of open subprocesses and pointers
#
# 1.0.3         Added functionality to support change of logging level without requiring restart
#               New check box in plugin prefs to support resetting device state to OFF when starting plugin
#               Improved thread awareness
#
# 1.0.4         Added new device state 'topicNotification' as a toggled boolean value that flips every time a message
#               is received.  This allows for triggers to be launched whenever a message is received, even if the
#               payload doesn't change
#
# 1.0.5         Fixed bug preventing correct status request message being sent
#
# 1.0.6         Added ability to stop 'noisy' topic/device from adding entries to log file
#
# 1.0.7         Fixed bugs in 1.0.6 release that stopped devices updating correctly
#
# 1.0.8         Corrected issue with onOffState updates when device did not support the property
#
# 1.1.0         Added support for basic authentication and embedding Indigo Variables when sending message to topic
#
#               Do I want to rewrite to remove threading???

import indigo

import Queue
import threading
import subprocess
import re


# Note the "indigo" module is automatically imported and made available inside
# our global name space by the host process.


class Plugin(indigo.PluginBase):
    ########################################
    # Main Functions
    ######################

    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)

        self.updatePrefs(pluginPrefs)

    def __del__(self):
        indigo.PluginBase.__del__(self)

    def updatePrefs(self, prefs):
        self.debug = prefs.get("mqttDebugInfo", False)
        self.resetState = prefs.get("mqttDefaultState", False)

        try:
            self.sleepinterval = int(prefs.get("mqttSleepPoll", 30))
        except:
            self.sleepinterval = 30

        if self.debug == True:
            indigo.server.log("mqtt debugging enabled")
        else:
            indigo.server.log("mqtt debugging disabled")

    def startup(self):
        self.debugLog("startup called")

        # set the global variables
        self.mqttProc = {}
        self.io_q = Queue.Queue()

        # start the queue reader
        threading.Thread(target=self.io_queue_reader).start()

    def shutdown(self):
        self.debugLog("shutdown called")

    def runConcurrentThread(self):
        try:
            self.debugLog("Starting ConcurrentThread ... Active listener threads = " + str(
                threading.activeCount() - 2))  # thread 1 = main, thread 2 = io_queue

            while True:
                for dev in indigo.devices.iter("self"):
                    if not dev.enabled or not dev.configured:
                        continue

                    try:
                        brokerName = dev.pluginProps["brokerName"]
                        brokerTopic = dev.pluginProps["brokerTopic"]

                        self.mqttProc[brokerName + brokerTopic].send_signal(0)
                    # self.mqttProc[dev.pluginProps["brokerName"] + dev.pluginProps["brokerTopic"]].send_signal(0)
                    except OSError:
                        # the process has stopped, restart it
                        self.deviceStartComm(dev)

                self.sleep(self.sleepinterval)

        except self.StopThread:
            pass  # Optionally catch the StopThread exception and do any needed cleanup.

    def deviceStartComm(self, dev):
        # start the mqtt listener thread for this device, storing the PID for future management
        dev.stateListOrDisplayStateIdChanged()

        if dev.enabled and dev.configured:
            # reset the device states if requested by user
            if self.resetState is True and dev.pluginProps["SupportsOnState"] is True:
                dev.updateStateOnServer("onOffState", value=0)

            brokerName = dev.pluginProps["brokerName"]
            brokerTopic = dev.pluginProps["brokerTopic"]

            try:
                brokerSecurity = dev.pluginProps["brokerSecurity"]
                brokerClientPrefix = dev.pluginProps["brokerClientPrefix"]
                brokerUsername = dev.pluginProps["brokerUsername"]
                brokerPassword = dev.pluginProps["brokerPassword"]
            except:
                brokerSecurity = False

            connectionString = ["/usr/local/bin/mosquitto_sub", "-h", brokerName, "-t", brokerTopic]

            if brokerSecurity == True:
                if brokerClientPrefix != "":
                    connectionString.extend(["-I", brokerClientPrefix])

                if brokerUsername != "":
                    connectionString.extend(["-u", brokerUsername, "-P", brokerPassword])

            # start the listener thread
            self.mqttProc[brokerName + brokerTopic] = subprocess.Popen(connectionString, stdout=subprocess.PIPE)

            retCode = self.mqttProc[brokerName + brokerTopic].returncode

            if str(retCode) != "None":
                self.debugLog(
                    "could not start mqtt listener for " + brokerName + ":" + brokerTopic + " failed with error code " + str(
                        retCode))
                return False

            threading.Thread(target=self.mqtt_listener, name=dev.name.replace(" ", ""), args=(
            self.mqttProc[brokerName + brokerTopic], brokerName, brokerTopic, self.resetState)).start()

    def deviceStopComm(self, dev):
        # stop the mqtt listener thread for this device
        if dev.enabled and dev.configured:
            try:
                self.mqttProc[dev.pluginProps["brokerName"] + dev.pluginProps["brokerTopic"]].terminate()
                self.debugLog(
                    "stopped mqtt_listener for " + dev.pluginProps["brokerName"] + ":" + dev.pluginProps["brokerTopic"])
            except:
                failed = 1

    ########################################
    # Sensor Functions
    ######################

    def mqtt_listener(self, proc, broker, topic, reset):
        updateText = "mqtt_listener for " + broker + ":" + topic + " started with pid: " + str(proc.pid)

        if reset == True:
            updateText = updateText + ", onOffState reset"

        self.debugLog(updateText)

        while True:
            line = proc.stdout.readline()

            if line != '':
                self.io_q.put([broker, topic, line.rstrip()])

            if proc.poll() != None:
                self.debugLog("mqtt_listener for " + broker + ":" + topic + " has stopped")
                break

    def io_queue_reader(self):
        self.debugLog("io_queue_reader started")

        onOffState = {"ON": 1, "OFF": 0}

        while True:
            try:
                broker, topic, item = self.io_q.get(True, 1)
            except Queue.Empty:
                empty = 1
            else:
                for dev in indigo.devices.iter("self"):
                    brokerName = dev.pluginProps["brokerName"]
                    brokerTopic = dev.pluginProps["brokerTopic"]

                    if [brokerName + brokerTopic] == [broker + topic]:
                        # incoming message related to this device

                        self.debugLog("io_queue_reader:" + broker + ":" + topic + ": " + item)

                        try:
                            if dev.pluginProps["muteTopic"] == False:
                                indigo.server.log("%s received mqtt message from %s" % (dev.name, topic))
                        except:
                            # error checking device property, set to False as default
                            dev.pluginProps["muteTopic"] = False
                            indigo.server.log("%s received mqtt message from %s" % (dev.name, topic))

                        try:
                            if item.upper() in ("ON", "OFF") and dev.pluginProps["SupportsOnState"] is True:
                                dev.updateStateOnServer("onOffState", value=onOffState[item.upper()])
                            else:
                                dev.updateStateOnServer("topicMessage", value=item)
                        except e:
                            indigo.server.log("error updating state, " + e)

                        # the value of the message may not change, so prompt that at least an update was received
                        try:
                            if dev.states["topicNotification"] == "0":
                                dev.updateStateOnServer("topicNotification", value="1")
                            else:
                                dev.updateStateOnServer("topicNotification", value="0")

                            self.debugLog("topic notification updated to %s" % dev.states["topicNotification"])
                        except:
                            dev.updateStateOnServer("topicNotification", value="0")
                            indigo.server.log("state error")

    ########################################
    # Custom Action Callbacks
    ######################

    def sendMessage(self, action, dev):
        brokerName = dev.pluginProps["brokerName"]
        brokerStatusTopic = dev.pluginProps["brokerStatusTopic"]

        try:
            brokerSecurity = dev.pluginProps["brokerSecurity"]
            brokerClientPrefix = dev.pluginProps["brokerClientPrefix"]
            brokerUsername = dev.pluginProps["brokerUsername"]
            brokerPassword = dev.pluginProps["brokerPassword"]
        except:
            brokerSecurity = False

        connectionString = ["/usr/local/bin/mosquitto_pub", "-h", brokerName, "-t", brokerStatusTopic]

        if brokerSecurity == True:
            if brokerClientPrefix != "":
                connectionString.extend(["-I", brokerClientPrefix])

            if brokerUsername != "":
                connectionString.extend(["-u", brokerUsername, "-P", brokerPassword])

        try:
            brokerStatusMessage = action.props.get("brokerMessage")

            # if the action message is blank, default to the one configured for the device
            if str(brokerStatusMessage) == "None":
                brokerStatusMessage = dev.pluginProps["brokerStatusMessage"]

            # check if variable needs to be sent
            try:
                brokerStatusMessage = re.sub(r"\%(\w+)\%", indigo.variables[
                    re.search(r"\%(\w+)\%", brokerStatusMessage).group(1)].value, brokerStatusMessage)

            except:
                self.debugLog("error trying to convert variable to string in message text")

        except:
            # something wrong in the text typed by the user
            self.debugLog("unable to correctly assign message for sending")
            return False

        connectionString.extend(["-m", brokerStatusMessage])

        self.debugLog(
            "sending message to " + brokerName + ":" + brokerStatusTopic + " with content " + brokerStatusMessage)
        p = subprocess.Popen(connectionString, stdout=subprocess.PIPE)
        p.wait()

        if p.returncode != 0:
            self.debugLog("could not send message; failed with error code " + str(p.returncode))
            return False

        indigo.server.log("sent \"%s\" message" % dev.name)

    ########################################
    # Inherited Action Callbacks
    ######################

    def actionControlGeneral(self, action, dev):
        if action.deviceAction == indigo.kDeviceGeneralAction.RequestStatus:
            # publish a status request message to the mqtt broker

            brokerName = dev.pluginProps["brokerName"]
            brokerTopic = dev.pluginProps["brokerStatusTopic"]
            brokerMessage = dev.pluginProps["brokerStatusMessage"]

            try:
                brokerSecurity = dev.pluginProps["brokerSecurity"]
                brokerClientPrefix = dev.pluginProps["brokerClientPrefix"]
                brokerUsername = dev.pluginProps["brokerUsername"]
                brokerPassword = dev.pluginProps["brokerPassword"]
            except:
                brokerSecurity = False

            connectionString = ["/usr/local/bin/mosquitto_pub", "-h", brokerName, "-t", brokerTopic]

            if brokerSecurity == True:
                if brokerClientPrefix != "":
                    connectionString.extend(["-I", brokerClientPrefix])

                if brokerUsername != "":
                    connectionString.extend(["-u", brokerUsername, "-P", brokerPassword])

            connectionString.extend(["-m", brokerMessage])

            self.debugLog(
                "sending status request to " + brokerName + ":" + brokerTopic + " with message " + brokerMessage)
            p = subprocess.Popen(connectionString, stdout=subprocess.PIPE)
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

        try:
            brokerSecurity = dev.pluginProps["brokerSecurity"]
            brokerClientPrefix = dev.pluginProps["brokerClientPrefix"]
            brokerUsername = dev.pluginProps["brokerUsername"]
            brokerPassword = dev.pluginProps["brokerPassword"]
        except:
            brokerSecurity = False

        connectionString = ["/usr/local/bin/mosquitto_pub", "-h", brokerName, "-t", brokerTopic]

        if brokerSecurity == True:
            if brokerClientPrefix != "":
                connectionString.extend(["-I", brokerClientPrefix])

            if brokerUsername != "":
                connectionString.extend(["-u", brokerUsername, "-P", brokerPassword])

        connectionString.extend(["-m", "IndigoTestMessage"])

        p = subprocess.Popen(connectionString, stdout=subprocess.PIPE)
        p.wait()

        if p.returncode != 0:
            self.debugLog(
                "Could not connect to the MQTT broker running at " + brokerName + ". Error code: " + str(p.returncode))
            e = indigo.Dict()
            e["brokerName"] = 1
            e["showAlertText"] = "Could not connect to the MQTT broker running at " + brokerName
            return (False, valuesDict, e)

        return (True, valuesDict)

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if userCancelled is False:
            self.updatePrefs(valuesDict)

        return (True, valuesDict)

