<!-- TOC -->

- [QKD-macsec](#qkd-macsec)
        - [Release](#release)
        - [Configurations](#configurations)
            - [Macsec](#macsec)
            - [Event-options](#event-options)
            - [Other necessary config](#other-necessary-config)
            - [Description of the macros](#description-of-the-macros)
        - [Sample logs](#sample-logs)
                - [SAE_A 127 secs interval](#sae_a-127-secs-interval)
                - [SAEB 61 seconds interval](#saeb-61-seconds-interval)

<!-- /TOC -->

# QKD-macsec

A script based approach to use keys derived from QKD and use it for macsec encryption. The script is supposed to be kept at /var/db/scripts/event/offbox.py
This script can be scheduled using event-options config in JUNOS.

1. SAE-A being a primary device queries the (key, keyID) pair from KME-A. Configures macsec with the key.
2. SAE-A sets the keyID at the interface description. This information is available to SAE-B since LLDP is enabled.
3. SAE-B uses the keyID to fetch the corresponding key and configure macsec using it.
4. When the macsec keys are synced on both ends a hitless rollover happens.

Since, the keys are applied asynchronously, there is a window where the configured keys will be out of sync on SAE-A & SAE-B.
During that period, the existing session using preceeding key will continue.

```
lab@SAE_A> show security mka sessions summary
Interface    Member-ID                  Type        Status        Tx        Rx        CAK Name
ge-0/0/1     1F51BB9778BCB24D17F4BE68   primary     live          34        3         ABCD1234ABCD5678ABCD1234ABCD56786C00260045EBAF1EBB0D41FBDD9FF375
ge-0/0/1     D0AC8A5F4D5A39B5DD9F649B   preceding   active        97        63        ABCD1234ABCD5678ABCD1234ABCD5678B4C9C6A1426AB71B1D53479F3D795B0D
```

### Release
```
lab@SAE_A> show version
Hostname: SAE_A
Model: srx380-poe-ac
Junos: 20.4R2.7
JUNOS Software Release [20.4R2.7]
```

### Configurations
#### Macsec
```
set security macsec connectivity-association CA_basic cipher-suite gcm-aes-xpn-256
set security macsec connectivity-association CA_basic security-mode static-cak
set security macsec connectivity-association CA_basic pre-shared-key ckn abcd1234abcd5678abcd1234abcd5678abcd1234abcd5678abcd1234abcd5678
set security macsec connectivity-association CA_basic pre-shared-key cak abcd1234abcd5678abcd1234abcd5678abcd1234abcd5678abcd1234abcd5678
set security macsec connectivity-association CA_basic exclude-protocol lldp
set security macsec interfaces ge-0/0/0 apply-macro qkd kme-ca false
set security macsec interfaces ge-0/0/0 apply-macro qkd kme-host etsi14-alice.keequant.com
set security macsec interfaces ge-0/0/0 apply-macro qkd kme-port 443
set security macsec interfaces ge-0/0/0 connectivity-association CA_basic
set security macsec interfaces ge-0/0/0 apply-macro qkd kme-keyid-check false
```
#### Event-options
```
set event-options generate-event every1min time-interval 60
set event-options policy qkd events every1min
set event-options policy qkd then event-script offbox.py
set event-options event-script file offbox.py python-script-user remote
set event-options traceoptions file script.log
set event-options traceoptions file size 10m
```
#### Other necessary config
```
set system host-name SAE_A
set system static-host-mapping etsi14-alice.keequant.com inet 78.46.89.177
set protocols lldp interface all
```

#### Description of the macros
| apply-macro | Use |
| ------ | ------ |
| kme-ca (true, false)| When set to true, uses HTTPS(SSL/TLS) to verify connection to KME |
| kme-host | Hostname or IP address of the KME connected to the SAE. Make sure you add a static host-name Junos config if you use hostname |
| kme-method (get, post) | Specifies the method used to fetch key with keyID in secondary device |
| kme-port | Port used to talk to KME. Should be set to 443, unless you need to experiment |
| kme-keyid-check (true, false) | If set to false, get/post call to KME will be made even if KeyId is stale. This macro is ignored for Primary SAE |



### Sample logs

##### SAE_A (127 secs interval)
```
2021-07-07 08:35:10,862 MainThread root INFO     Logging modules initialized successfully
2021-07-07 08:35:10,864 MainThread root INFO     Onbox approach taken
2021-07-07 08:35:18,455 MainThread root INFO     ------------------- processing link ge-0/0/1 --------------------
2021-07-07 08:35:18,456 MainThread root INFO     SAE_A - SAE_B
2021-07-07 08:35:18,460 MainThread root INFO     Using certs from /var/home/lab/certs/
2021-07-07 08:35:18,462 MainThread root INFO     base url: https://ip-172-31-42-131.ec2.internal:443/api/v1/keys/
2021-07-07 08:35:18,700 MainThread root INFO     KME: Get Status API: https://ip-172-31-42-131.ec2.internal:443/api/v1/keys/SAE_B/status
2021-07-07 08:35:18,701 MainThread root INFO     SAE_A is Master
2021-07-07 08:35:18,762 MainThread root INFO     KME: Get Keys API: https://ip-172-31-42-131.ec2.internal:443/api/v1/keys/SAE_B/enc_keys
2021-07-07 08:35:18,763 MainThread root INFO     Received KeyId:eb54c2cc-4419-bf37-4910-79dcf59d9c4a Key:nhlywZqo3fTVZeWkTYcC18rTt+C2ZAcCAZIc+6UGaTM=
2021-07-07 08:35:18,768 MainThread root INFO     ------------------- processing link ge-0/0/14 --------------------
2021-07-07 08:35:18,769 MainThread root INFO     SAE_A - SAE_B
2021-07-07 08:35:18,772 MainThread root INFO     Using certs from /var/home/lab/certs/
2021-07-07 08:35:18,774 MainThread root INFO     base url: https://ip-172-31-42-131.ec2.internal:443/api/v1/keys/
2021-07-07 08:35:18,822 MainThread root INFO     KME: Get Status API: https://ip-172-31-42-131.ec2.internal:443/api/v1/keys/SAE_B/status
2021-07-07 08:35:18,823 MainThread root INFO     SAE_A is Master
2021-07-07 08:35:18,872 MainThread root INFO     KME: Get Keys API: https://ip-172-31-42-131.ec2.internal:443/api/v1/keys/SAE_B/enc_keys
2021-07-07 08:35:18,874 MainThread root INFO     Received KeyId:58ce07a7-4308-a1ad-0216-a993a083a1ff Key:exqdO9xLVZ4bnYsTmurRk9AU/y8YYfU+6wg2+3L+jvQ=
2021-07-07 08:35:20,504 MainThread root INFO     ========================== script run SUCCESS =============================
```
##### SAEB (61 seconds interval)
```
2021-07-07 08:36:01,876 MainThread root INFO     Logging modules initialized successfully
2021-07-07 08:36:01,878 MainThread root INFO     Onbox approach taken
2021-07-07 08:36:09,535 MainThread root INFO     ------------------- processing link ge-0/0/1 --------------------
2021-07-07 08:36:09,537 MainThread root INFO     SAE_B - SAE_A
2021-07-07 08:36:09,541 MainThread root INFO     Using certs from /var/home/lab/certs/
2021-07-07 08:36:09,543 MainThread root INFO     base url: https://ip-172-31-44-229.ec2.internal:443/api/v1/keys/
2021-07-07 08:36:09,770 MainThread root INFO     KME: Get Status API: https://ip-172-31-44-229.ec2.internal:443/api/v1/keys/SAE_A/status
2021-07-07 08:36:09,772 MainThread root INFO     SAE_B is Slave
2021-07-07 08:36:09,773 MainThread root INFO     Previous keyID: e7a061e0-414c-81be-cbd6-02c1e4627beb
2021-07-07 08:36:09,821 MainThread root INFO     KME: Get Key with key_ID: eb54c2cc-4419-bf37-4910-79dcf59d9c4a
2021-07-07 08:36:09,822 MainThread root INFO     KME: Received Key: nhlywZqo3fTVZeWkTYcC18rTt+C2ZAcCAZIc+6UGaTM=
2021-07-07 08:36:09,826 MainThread root INFO     ------------------- processing link ge-0/0/14 --------------------
2021-07-07 08:36:09,827 MainThread root INFO     SAE_B - SAE_A
2021-07-07 08:36:09,831 MainThread root INFO     Using certs from /var/home/lab/certs/
2021-07-07 08:36:09,833 MainThread root INFO     base url: https://ip-172-31-44-229.ec2.internal:443/api/v1/keys/
2021-07-07 08:36:09,879 MainThread root INFO     KME: Get Status API: https://ip-172-31-44-229.ec2.internal:443/api/v1/keys/SAE_A/status
2021-07-07 08:36:09,880 MainThread root INFO     SAE_B is Slave
2021-07-07 08:36:09,881 MainThread root INFO     Previous keyID: aa537948-428c-a52f-fff2-da95320f8e93
2021-07-07 08:36:10,165 MainThread root INFO     KME: Get Key with key_ID: 58ce07a7-4308-a1ad-0216-a993a083a1ff
2021-07-07 08:36:10,167 MainThread root INFO     KME: Received Key: exqdO9xLVZ4bnYsTmurRk9AU/y8YYfU+6wg2+3L+jvQ=
2021-07-07 08:36:10,474 MainThread root INFO     ========================== script run SUCCESS =============================
2021-07-07 08:37:02,858 MainThread root INFO     Logging modules initialized successfully
2021-07-07 08:37:02,860 MainThread root INFO     Onbox approach taken
2021-07-07 08:37:10,773 MainThread root INFO     ------------------- processing link ge-0/0/1 --------------------
2021-07-07 08:37:10,775 MainThread root INFO     SAE_B - SAE_A
2021-07-07 08:37:10,778 MainThread root INFO     Using certs from /var/home/lab/certs/
2021-07-07 08:37:10,781 MainThread root INFO     base url: https://ip-172-31-44-229.ec2.internal:443/api/v1/keys/
2021-07-07 08:37:11,209 MainThread root INFO     KME: Get Status API: https://ip-172-31-44-229.ec2.internal:443/api/v1/keys/SAE_A/status
2021-07-07 08:37:11,211 MainThread root INFO     SAE_B is Slave
2021-07-07 08:37:11,212 MainThread root INFO     Same KeyId: eb54c2cc-4419-bf37-4910-79dcf59d9c4a, Skipping ge-0/0/1
2021-07-07 08:37:11,223 MainThread root INFO     ------------------- processing link ge-0/0/14 --------------------
2021-07-07 08:37:11,224 MainThread root INFO     SAE_B - SAE_A
2021-07-07 08:37:11,227 MainThread root INFO     Using certs from /var/home/lab/certs/
2021-07-07 08:37:11,229 MainThread root INFO     base url: https://ip-172-31-44-229.ec2.internal:443/api/v1/keys/
2021-07-07 08:37:11,289 MainThread root INFO     KME: Get Status API: https://ip-172-31-44-229.ec2.internal:443/api/v1/keys/SAE_A/status
2021-07-07 08:37:11,291 MainThread root INFO     SAE_B is Slave
2021-07-07 08:37:11,292 MainThread root INFO     Same KeyId: 58ce07a7-4308-a1ad-0216-a993a083a1ff, Skipping ge-0/0/14
2021-07-07 08:37:11,295 MainThread root INFO     ========================== script run SUCCESS =============================
```
