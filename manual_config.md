## Manual configuration through Configuration.yaml (deprecated, I'll likely remove this in the future)

This is an alternative to the configuration above if you prefer manual configuration. Add to Configuration.yaml (replace with your specific setup):

```
mlgw:
  host: 192.168.1.10
  username: admin
  password: <your password>
  use_mllog: true
  default_source: A.MEM
  available_sources:
    - A.MEM
    - CD
    - RADIO
  devices:
    - name: Living Room
    - name: Kitchen
    - name: Patio
    - name: Studio
    - name: TV Room
    - name: Bedroom
    - name: Bathroom
```

Add the devices in the same order as the devices in the MLGW/BLGW configuration. The MLGW setup page is found in Setup -> Programming -> Devices -> MasterLink products. Each device must have a unique MLN and must be assigned using the buttons under _MasterLink products assignment_ further down on the same page.

If you don't set a MLN (masterlink node number) for the devices, they need to be defined in the same order as the MLGW configuration, and MLNs will be assigned sequentially, starting from 1 for the first one. For example, the yaml configuration above, correspond to the MLGW configuration in the picture above.

If you want to set specific MLNs then you can change the devices section to something like this. Note that if you give wrong MLNs the plugin won't work or might operate the wrong device.

```
  devices:
    - name: Living Room
      mln: 9
    - name: Kitchen
      mln: 11
```

You can also add a room number, corresponding to your MLGW configuration, and force a Masterlink ID for a device.

```
  devices:
    - name: Living Room
      room: 9
      id: VIDEO_MASTER
    - name: Kitchen
      room: 1
```

Put any Network Link devices at the end of the list. They typically start at MLN 20. If you don't, the Direct Master Link Connection feature will get confused and the system will not work as intended.

For the undocumented 'Direct MasterLink' connection to work, you must use **'username: admin'**, and the admin password as credentials for the MLGW and set **'use_mllog: true'** in the configuration.

