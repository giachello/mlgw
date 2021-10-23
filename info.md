[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/custom-components/hacs) [![mlgw](https://img.shields.io/github/release/giachello/mlgw.svg?1)](https://github.com/giachello/mlgw) ![Maintenance](https://img.shields.io/maintenance/yes/2021.svg)


# MasterLink Gateway / BeoLink Gateway
Home Assistant custom component to integrate Bang & Olufsen MasterLink Gateway and BeoLink Gateway.

This component connects to the MasterLink and Beolink Gateway and makes all your Bang & Olufsen audio and video devices into "media_player" entities in Home Assistant. It uses an undocumented feature that enables functionality normally not provided even by the Bang & Olufsen official apps, like using your B&O remotes to control streaming devices on Home Assistant. 

# Minimal Configuration

## Minimum Home Assistant version

MLGW is compatible with any version since 2021.1.1

## Setup

To setup the integration, install it through Hacs or by copying the files in your `custom_components/mlgw` folder. Then, the Bang & Olufsen devices should show up automatically in the Integrations panel in Home Assistant.

More information here: ![README.md](https://github.com/giachello/mlgw/blob/main/README.md)
