# mqttGateway
MQTT Plugin for Indigo

I’ve been working on a number of projects that use the MQTT protocol to publish device state changes and have been using a Mosquitto Broker running on my Indigo server for quite a while now. I’ve just finished development of the v1 release of my MQTT Gateway for Indigo. The plugin allows you to create a set of 'Sensor' devices that correspond to specific Topics on the Broker. Threading is used to support multiple Topics with dedicated listeners.

Features

1. Create an Indigo device for each MQTT topic that you want to subscribe to
2. If an ‘On’ or ‘Off’ is broadcast on the specific topic, the device onOffState is updated
3. Any other message broadcast on the same topic will result in the topicMessage value being updated
4. Supports Triggers and Status Requests (your corresponding MQTT device will need to implement the response)
5. Provides an Action for sending any message to the subscribed topic

Pre-requisites

A working MQTT Broker supporting MQTT v3.1. I've used and tested this build with Mosquitto 1.4. Visit the following link for instructions to set one up if you need some guidance - http://wp.me/p6M2U5-3q
