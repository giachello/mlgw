# mlgw

Support for Bang & Olufsen Master Link Gateway and Beolink Gateway.

This component manages communication To and From the MLGW.

Light commands to control Hass.io lights are captured by listenin to Virtual Button and Light events fired by the platform.

Configuration example:

mlgw:
  host: 192.168.1.10
  username: usr00
  password: usr00
  port: 9000
  use_ml: true
  default_source: A.MEM
  available_sources:
    - A.MEM
    - CD
    - RADIO
  devices:
    - name: BeoSound
    - name: BeoLab3500LR
    - name: Patio
    - name: BeoLabStudio
    - name: TVRoom
    - name: Bedroom
    - name: Bathroom

Devices need to be defined in the same order as the MLGW configuration, and MLNs need to be sequential, starting from 1 for the first one.
