ssh admin@10.54.12.193 "cat /var/db/scripts/op/qkd_qfx2.py" > qkd_qfx2.py
sha256sum qkd_qfx2.py 
cb1ad25249c12f0ba4dd8a112646a2ca2065c816cc4ded684fccb8ac054bda06  qkd_qfx2.py
admin@vqfx-2# set system scripts op file qkd_qfx2 checksum sha-256 cb1ad25249c12f0ba4dd8a112646a2ca2065c816cc4ded684fccb8ac054bda06 



% echo "Mq00wV8BIMhTuLTc5cjySbwEotGdDBVezxufQ6Q3WjbOoojFHuCfeZkbNq2m7uKSLkj6DKrQJK1Ox6VK7O8qqqh3fzMGM+BQhMGhy3Sk95O361NtE5ev2bvv009B4Tu5HNKcMJlRpGCu+LuPtCYzCwLZITx/sipISFGbNaUV7UY="| base64 -d | wc -c 
     128
    This. means the key is 1024 bits. Instead we should enc/dec with 256 key lenght. 



% curl -s "https://100.100.100.10:8443/api/v1/keys/sae_002/enc_keys?key_size=256" --cert /var/db/scripts/certs/sae_001.crt --key /var/db/scripts/certs/sae_001.key --cacert /var/db/scripts/certs/offbox_rootCA.crt
{"keys":[{"key_ID":"2cfed7bf-8f2a-497c-83d3-1d934fb11dfb","key":"IfPJiWIPmS1U6lYmXaY9o+eyqOwfl7gR/xamO1iaUGHmRm0vFdGvCy5UpnBtQuBphjXR1o/NMlejWGP63QAnRRfXrMPokw4bdHMcTSwv4IqMvZbm9thHJCN0Winij82yNfOVx9kxa0WGcuSEdrnJznqdgSiLT4cHSsuxKNnyLQw="}]}% 

echo "IfPJiWIPmS1U6lYmXaY9o+eyqOwfl7gR/xamO1iaUGHmRm0vFdGvCy5UpnBtQuBphjXR1o/NMlejWGP63QAnRRfXrMPokw4bdHMcTSwv4IqMvZbm9thHJCN0Winij82yNfOVx9kxa0WGcuSEdrnJznqdgSiLT4cHSsuxKNnyLQw=" | base64 -d | wc -c


# System
Static MACsec + external key rotation driven by QKD or 
QKD-assisted MACsec key rotation (static CAK mode)

## Keys:

fetched via KME (enc_keys / dec_keys)
pushed via CLI + commit

## Rekey:

done via script (event/op script + SSH)

## What you do NOT have

No MKA (MACsec Key Agreement)
No 802.1X
No protocol-level rekey negotiation
No live session rekey without config push





# ROLES AND TOPOLOGIES
you need roles must be per link not per device 

## PAIR OF TWO NODES
python3 qkd_orchestrator.py create \
  --mode qkd \
  --topology pair \
  --devices qfx1 qfx2 \
  --ips 10.0.0.1 10.0.0.2 \
  --interfaces \
    xe-0/0/0 \
    xe-0/0/0 \
  --kmes 100.0.0.1 100.0.0.2

## CHAIN
% python3 qkd_orchestrator.py create \
  --mode qkd \
  --topology chain \
  --devices qfx1 qfx2 qfx3 qfx4 \
  --ips 10.0.0.1 10.0.0.2 10.0.0.3 10.0.0.4 \
  --interfaces xe-0/0/0 xe-0/0/0,xe-0/0/1 xe-0/0/0,xe-0/0/1 xe-0/0/0  \
  --kmes 100.0.0.1 100.0.0.2 100.0.0.3 100.0.0.4
✅ Inventory generated (qkd)
✅ Inventory created (chain, mode=qkd)
✅ PKI already exists - skipping generation

## HUB SPOKE
% python3 qkd_orchestrator.py create \
  --mode qkd \
  --topology hub \
  --hub qfx1 \
  --devices qfx1 qfx2 qfx3 qfx4 \
  --ips 10.0.0.1 10.0.0.2 10.0.0.3 10.0.0.4 \
  --interfaces \
    xe-0/0/0,xe-0/0/1,xe-0/0/2 \
    xe-0/0/0 \
    xe-0/0/0 \
    xe-0/0/0 \
  --kmes 100.0.0.1 100.0.0.2 100.0.0.3 100.0.0.4

## RING
python3 qkd_orchestrator.py create \
  --mode qkd \
  --topology ring \
  --devices qfx1 qfx2 qfx3 qfx4 \
  --ips 10.0.0.1 10.0.0.2 10.0.0.3 10.0.0.4 \
  --interfaces \
    xe-0/0/0,xe-0/0/1 \
    xe-0/0/0,xe-0/0/1 \
    xe-0/0/0,xe-0/0/1 \
    xe-0/0/0,xe-0/0/1 \
  --kmes 100.0.0.1 100.0.0.2 100.0.0.3 100.0.0.4


##  Another issue: CONNECTIVITY ASSOCIATION
For each link:
✅ Both sides must use the same CA name at the same time
✅ You must support make-before-break (2 active CAs)
✅ Rotation must be independent per link

CAs must be per-link, not per-device
Each link is a completely independent CA domain
instead of CA1, CA2 (global)
I need link-id:(CA_A, CA_B)


## Graceful degradation + controlled failure of MACsec when QKD is unavailable

### Case 1 — QKD fails temporarily

enc/dec fails
MACsec currently UP

DO NOTHING

* keep current CA active
* no disruption
* keep using last installed key

### Case 2 — QKD down for a while

multiple retries failed

START TIMER

but still keep MACsec UP

### Case 3 — QKD down too long
tear down MACsec



### PROBLEM

Timeline


* t0
* 
* qfx1 = CA1 ✅
* qfx2 = CA1 ✅



* t1 → QKD down on qfx2
* 
* qfx1 still working (it can enc)
* qfx2 cannot dec



qfx2 stays on CA1 ✅


* t60 (rotation triggers on master qfx1)
* 
* qfx1 switches → CA2 ❗
* qfx2 still on CA1 ❗

 💥 MACsec breaks (CKN/CA mismatch)

System must be synced per link, not master slave driven! 
❗ A link MUST NOT rotate unless BOTH sides are ready

Let's create a guard + recovery + anti-flap method: 

### ✅ Case 1 — QKD glitch
fail < timeout → do nothing ✅


### ✅ Case 2 — QKD long outage
fail > timeout → MACsec DOWN ✅


### ✅ Case 3 — QKD returns
wait RECOVERY_GRACE
→ re-sync
→ MACsec UP ✅

### ✅ Case 4 — Flapping avoided
rapid fail/recover → blocked by grace timers ✅





# switch keys


t=0

CA1 = key1   <-- active
CA2 = key2   <-- standby

## after 60 seconds: 

interface -> CA2
commit


CA1 = key1
CA2 = key2   <-- active

## now reprogam

ENC -> key3

reprogram CA1 = key3
commit

CA1 = key3   <-- standby
CA2 = key2   <-- active

## after 60 seconds
interface -> CA1
commit
## and after: 
ENC -> key4

reprogram CA2 = key4
commit

## cycle is like this: 
switch
rebuild inactive CA
switch
rebuild inactive CA
switch
rebuild inactive CA

## cycle is NOT: 
rebuild target CA
switch
rebuild target CA
switch

Questa è esattamente la logica di un buffer circolare a due slot.
la chiave on deve essere usata immediatamente per il traffico.
Deve essere caricata nella CA inattiva per il prossimo giro.
## log becomes
* ACTIVE CA=CA1 key_id=key1
* STANDBY CA=CA2 key_id=key2
* ROTATE TO CA2
* ACTIVE CA=CA2 key_id=key2
* REFRESH STANDBY CA1 WITH key_id=key3

stai programmando la CA che stai per attivare invece della CA che hai appena disattivato.

## ciclo e'cosi: 
t0
ENC -> key1
ENC -> key2

CA1 = key1 (active)
CA2 = key2 (standby)

t60
switch -> CA2

ENC -> key3
program CA1 = key3

t120
switch -> CA1

ENC -> key4
program CA2 = key4


set CMD='curl -s --cert /var/db/scripts/certs/sae_001.crt --key /var/db/scripts/certs/sae_001.key --cacert /var/db/scripts/certs/offbox_rootCA.crt https://100.100.100.10:8443/api/v1/keys/sae_002/enc_keys?key_size=256'

foreach i (1 2 3 4 5)
    echo "=== REQUEST $i ==="
    eval "$CMD"
    echo
end



docker network inspect qkd_net | grep -E 'kme|IPv4Address'



deploy
 ├─ generate ssh key (if missing)
 ├─ copy private/public key to all devices
 ├─ install public key into authorized_keys on all devices
 ├─ verify device-to-device ssh
 ├─ copy qkd_onbox.py
 ├─ copy certs
 └─ enable event/op scripts

## deploy on devices
  - legge config/runtime/devices.yaml
  - usa ssh_trust.local_private_key / local_public_key
  - crea /var/home/<script_user>/.ssh sui device
  - copia qkd_id_rsa e qkd_id_rsa.pub
  - aggiunge public_key in authorized_keys
  - sistema i permessi


%python3 qkd_orchestrator.py create \ 
  --devices qfx1 qfx2 \
  --ips 10.54.13.14 10.54.12.193 \                         
  --interfaces xe-0/0/0 xe-0/0/1 \
  --kmes 100.100.100.10 100.100.100.11 \
  --platform qfx \
  --topology pair \
  --mode qkd
🧹 Cleaning local runtime only: /Users/aterren/Lavoro 2026/quantum 2026/newMACSEC37/config/runtime
🔐 Generating SSH keypair: /Users/aterren/Lavoro 2026/quantum 2026/newMACSEC37/config/runtime/ssh/qkd_id_rsa
Generating public/private rsa key pair.
Your identification has been saved in /Users/aterren/Lavoro 2026/quantum 2026/newMACSEC37/config/runtime/ssh/qkd_id_rsa
Your public key has been saved in /Users/aterren/Lavoro 2026/quantum 2026/newMACSEC37/config/runtime/ssh/qkd_id_rsa.pub
The key fingerprint is:
SHA256:l4RpMw0v6YfXKggikWTKzseFxArqbgpVfsLj4LvIlNM qkd-orchestrator-andrea.terren@hpe.com
The key's randomart image is:
+---[RSA 4096]----+
| o..    .        |
|*....    B       |
|*.....  O +      |
|+o.+.  o * o     |
|oo+o* . S = .    |
| =++ = . + .     |
|o+.E. . . .      |
|++..     .       |
|=.o.             |
+----[SHA256]-----+
✅ SSH keypair generated
✅ Inventory generated (qkd)
✅ Inventory created (pair, mode=qkd)
✅ PKI already exists - skipping generation




%cli -c "configure; delete security macsec interfaces xe-0/0/1; commit; exit"

%cli -c "show configuration security macsec interfaces | display set"





## CLEAN
You said:
this is my function can you give me the full fix : def bootstrap_link(link, force=False):

    peer = link["peer"]
    iface = link["interface"]

    ca1 = link["ca_names"][0]
    ca2 = link["ca_names"][1]

    state = load_db_state(peer, iface, link["ca_names"])
    generation = next_generation(state)
    
    if not force and validate_state(state, link["ca_names"]):
        return True

    log(
        "DOUBLE BUFFER BOOTSTRAP START",
        "INFO",
        iface,
        "BOOTSTRAP"
    )

    #
    # KEY FOR CA1
    #
    key1_id, key1 = do_enc(link["peer_sae"])

    if not key1_id:
        log(
            "BOOTSTRAP FAILED key1",
            "ERROR",
            iface,
            "BOOTSTRAP"
        )
        return False

    if not send_command(
            link,
            "program",
            iface,
            ca=ca1,
            key_id=key1_id,
            generation=generation
        ):
        log(
            "BOOTSTRAP FAILED peer program CA1",
            "ERROR",
            iface,
            "BOOTSTRAP"
        )
        return False

    time.sleep(0.5)

    if not program_ca(
        iface,
        key1_id,
        key1,
        ca1
    ):
        log(
            "BOOTSTRAP FAILED local program CA1",
            "ERROR",
            iface,
            "BOOTSTRAP"
        )
        return False

    state["ca_keys"][ca1] = key1_id

    #
    # KEY FOR CA2
    #
    key2_id, key2 = do_enc(link["peer_sae"])

    if not key2_id:
        log(
            "BOOTSTRAP FAILED key2",
            "ERROR",
            iface,
            "BOOTSTRAP"
        )
        return False

    if not send_command(
        link,
        "program",
        iface,
        ca=ca2,
        key_id=key2_id,
        generation=generation
    ):
        log(
            "BOOTSTRAP FAILED peer program CA2",
            "ERROR",
            iface,
            "BOOTSTRAP"
        )
        return False

    time.sleep(0.5)

    if not program_ca(
        iface,
        key2_id,
        key2,
        ca2
    ):
        log(
            "BOOTSTRAP FAILED local program CA2",
            "ERROR",
            iface,
            "BOOTSTRAP"
        )
        return False

    state["ca_keys"][ca2] = key2_id

    #
    # ACTIVATE CA1 ON PEER
    #
    if not send_command(
        link,
        "activate",
        iface,
        ca=ca1,
        generation=generation
    ):
        log(
            "BOOTSTRAP PEER ACTIVATE FAIL",
            "ERROR",
            iface,
            "BOOTSTRAP"
        )
        return False

    time.sleep(0.5)

    #
    # ACTIVATE CA1 LOCALLY
    #
    if not activate_ca(iface, ca1):
        log(
            "BOOTSTRAP LOCAL ACTIVATE FAIL",
            "ERROR",
            iface,
            "BOOTSTRAP"
        )
        return False

    state["active_ca"] = ca1
    state["generation"] = generation
    state["last_rotation"] = int(time.time())

    save_db_state(
        peer,
        iface,
        state
    )

    log(
        f"BUFFER "
        f"active={ca1}:{state['ca_keys'].get(ca1)} "
        f"standby={ca2}:{state['ca_keys'].get(ca2)}",
        "INFO",
        iface,
        "BOOTSTRAP"
    )

    log(
        f"DOUBLE BUFFER READY active={ca1} standby={ca2}",
        "INFO",
        iface,
        "BOOTSTRAP"
    )

    return True
Copilot said:
Copilot

Sì. Sostituisci tutta la tua bootstrap_link() con questa versione.

Fix principale:

    se force=True, lo state viene resettato con default_db_state()
    la generation viene incrementata dal vecchio state
    dopo bootstrap, lo state viene salvato con active_ca = ca1
    il log include anche generation
    niente cambio alla logica peer/interface

Python

Poi fai:
Shell

Se vuoi resettare lo stato sporco attuale sui device:
Shell

Oppure lascia che il prossimo forced bootstrap lo riallinei. Con questa fix, dopo bootstrap dovrebbe smettere di ripetere:
Plain Text
You said:
with the clean option what do i clean exactly ?
Copilot said:
Copilot

With the clean option, ideally you clean three different scopes:
1. Local runtime cleanup

This removes generated runtime artifacts on your Mac:
Plain Text

That includes things like:
Plain Text

So after local clean, you need to run create again.
2. Remote device cleanup

On each QFX, clean should remove/reset:
MACsec config
Shell

This removes:
Plain Text

This is important because otherwise stale interfaces like:
Plain Text

can remain configured on the wrong box.
Onbox scripts

Remove:
Plain Text

Commands:
Shell
Temporary QKD runtime state

Remove:
Plain Text

Commands:
Shell

This resets the double-buffer state.
Temporary deploy copy

If you SCP qkd_onbox.py temporarily to /var/tmp, clean should also remove it:
Shell

But better: deploy_onbox() should already remove it after installing:
Shell
SSH trust keys

If you deploy QKD SSH keys under:
Plain Text

then clean should remove:
Plain Text

and ideally remove the corresponding public key from:
Plain Text

So remote clean should use ssh_trust from devices.yaml:
YAML
3. Optional PKI cleanup

Only if you run:
Shell

then you also remove local PKI material:
Plain Text

That means root CA, SAE certs, keys, etc.

Without --pki, I would not delete certs.
What clean should do in your flow
Normal clean
Shell

Should do:
Plain Text
Local only clean
Shell

Should do only:
Plain Text

No device access.
Full local + remote + PKI clean
Shell

Should do:
Plain Text
Important ordering

The clean order must be:
Plain Text

Not the opposite.

Because if you delete config/runtime/devices.yaml first, then the orchestrator no longer knows:
Plain Text

and remote clean cannot work.

So your handle_clean() should not call clean_runtime() before reading devices.yaml.
In your current case

Given your issue with wrong MACsec interface config, you want clean to remove at least:
Shell

That ensures:
Plain Text

Then you rerun:
Shell

Then trigger the script again.
You said:
with the clean, i still see description "MACsec link towards qfx2 (xe-0/0/1)"; being applied to the local interface in qfx1... and also i see the event-option remote, etc.. (venv) aterren@aterren-mbp newMACSEC37 % python3 qkd_orchestrator.py clean    
Using runtime devices.yaml
🧹 [qfx1] Cleaning device 10.54.13.14
✅ [qfx1] Device clean complete
🧹 [qfx2] Cleaning device 10.54.12.193
✅ [qfx2] Device clean complete
🧹 Cleaning local runtime only: /Users/aterren/Lavoro 2026/quantum 2026/newMACSEC37/config/runtime
Full clean complete
Copilot said:
Copilot

Sì, adesso il problema è chiaro: il tuo clean attuale pulisce MACsec e file runtime, ma non pulisce tutta la config Junos che l’orchestrator/provisioning ha creato.

In particolare ti mancano almeno:
Plain Text

Quindi il clean dice:
Plain Text

ma in realtà sta lasciando roba configurata.
1. Prima: fix deploy_onbox() per rimuovere /var/tmp/qkd_onbox.py

Nel tuo deploy_onbox(), nel comando install_cmd, aggiungi il rm.

Deve essere così:
Python

Così /var/tmp/qkd_onbox.py viene usato solo come staging temporaneo e poi rimosso.
2. Sostituisci clean_device() con questa versione completa

Questa versione:

    usa PyEZ come deploy, quindi usa password da devices.yaml
    cancella security macsec
    cancella event-options
    cancella op script registration
    cancella file script
    cancella /var/tmp/qkd_*
    cancella /var/tmp/qkd_onbox.py
    cancella le descrizioni sulle interfacce coinvolte, sia local che peer, per rimuovere eventuale sporco lasciato da run precedenti

Python
3. Nota sulla description

Se su qfx1 vedi una description tipo:
Plain Text

sotto:
Plain Text

allora non è necessariamente sbagliata: può voler dire “questa interfaccia locale va verso qfx2 peer interface xe-0/0/1”.

È sbagliata se su qfx1 vedi configuration sotto:
Plain Text

perché xe-0/0/1 è la peer interface, non la local interface di qfx1.

Comunque la versione di clean_device() sopra rimuove description sia da interface sia da peer_interface, così pulisce anche eventuale sporco generato da versioni vecchie.
4. Ora attenzione: hai appena cancellato config/runtime

Il tuo clean ha finito con:
Plain Text

Quindi ora per rilanciare clean remoto con la nuova funzione devi prima rigenerare runtime:
Shell

Poi:
Shell
5. Verifica dopo clean su qfx1

Su qfx1:
Shell

Dopo clean non dovresti più vedere:
Plain Text
6. Poi redeploy pulito

Dopo che il clean è veramente pulito:
Shell
Shell

Poi su qfx1 controlla:
Shell

Expected:
Plain Text

Non deve esserci:
Plain Text
You said:
inoltre qui ho diverse opzioni ma quando le uso ? clean = subparsers.add_parser("clean")
    clean.add_argument("--local-only", action="store_true")
    clean.add_argument("--pki", action="store_true", help="Also remove local PKI certs")
Copilot said:
Copilot

Sì, quelle opzioni hanno senso, ma vanno chiarite bene perché oggi il comportamento può confondere.

Hai:
Python

Quindi ad oggi devi pensarle così.
1. clean senza opzioni
Shell

Questo dovrebbe essere il full lab reset standard, cioè:

    legge config/runtime/devices.yaml
    pulisce i device remoti:
        security macsec
        event-options QKD
        op/event script registration
        /var/db/scripts/op/qkd_onbox.py
        /var/db/scripts/event/qkd_onbox.py
        /var/tmp/qkd_*
        /var/tmp/qkd_debug.log*
        eventuale temporary /var/tmp/qkd_onbox.py
        opzionalmente QKD SSH key sotto /var/home/<script_user>/.ssh/
    poi cancella runtime locale:
        config/runtime/devices.yaml
        config/runtime/topology.yaml
        config/runtime/ssh/qkd_id_rsa
        config/runtime/ssh/qkd_id_rsa.pub
        config/runtime/<device>/qkd_onbox.py

## local-only 
deletes only config/runtime, no remote devices

## pki
with --pki option 
remote device clean
local runtime clean
local certs clean




show security macsec connections | match "Interface name|CA name|Status|Create time"

while true; do
    date
    cli -c 'show security macsec connections | match "Interface name|CA name|Status: inuse"'
    cli -c 'show security macsec statistics | match "Interface name|Encrypted packets|Encrypted bytes|Accepted packets|Decrypted bytes"'
    echo "----"
    sleep 5
done



cat > /var/tmp/watch_macsec.sh << EOF
#!/bin/sh

while true
do
    date
    cli -c "show security macsec connections" | grep -E "Interface name|CA name|Status: inuse"
    cli -c "show security macsec statistics" | grep -E "Interface name|Encrypted packets|Encrypted bytes|Accepted packets|Decrypted bytes"
    echo "----"
    sleep 5
done
EOF

chmod +x /var/tmp/watch_macsec.sh
/bin/sh /var/tmp/watch_macsec.sh



cat > /var/tmp/watch_macsec.sh << EOF
#!/bin/sh

while true
do
    date
    cli -c "show security macsec connections" | grep -E "Interface name|CA name|Status: inuse|Create time"
    cli -c "show security macsec statistics" | grep -E "Interface name|Encrypted packets|Encrypted bytes|Accepted packets|Decrypted bytes"
    echo "----"
    sleep 5
done
EOF

chmod +x /var/tmp/watch_macsec.sh
/bin/sh /var/tmp/watch_macsec.sh


rm -f /var/db/scripts/certs/sae_*.crt
rm -f /var/db/scripts/certs/sae_*.key
rm -f /var/db/scripts/certs/sae_*.key
rm -f /var/db/scripts/certs/*.crt
rm -f /var/db/scripts/certs/*.key
rm -f /var/db/scripts/certs/*.crt
mkdir -p /var/db/scripts/certs
chown -R admin:admin /var/db/scripts/certs
chmod 755 /var/db/scripts/certs



cd /root/new_qkd_dist

cp certs/offbox_rootCA.crt certs/kme/root.crt
cp certs/offbox_rootCA.key certs/kme/root.key

mkdir -p /root/etsi-gs-qkd-014-referenceimplementation/certs/

cp -av certs/kme/* /root/etsi-gs-qkd-014-referenceimplementation/certs/

docker ps --format '{{.Names}}' | grep -i kme | xargs -r docker restart




Adesso il tuo comportamento è troppo aggressivo:

```text
KME3 down
=> sae_003 DEC failed
=> master continua/rebootstrap/activate
=> rischio CA attiva senza key corretta
=> MACsec non mostra più Status: inuse
```

Invece il comportamento corretto deve essere:

```text
KME down temporaneo
=> NON distruggere MACsec
=> NON rebootstrap
=> NON attivare CA nuova se peer non ha programmato la key
=> tieni l'ultima CA valida in use
=> marca link degraded
=> retry per N cicli
=> se dopo 1 ora è ancora down, allora dichiara link down e fai macsec_down()
```

***

# Politica corretta

Per ogni link devi introdurre uno stato tipo:

```json
{
  "generation": 569,
  "active_ca": "CA3",
  "ca_keys": {
    "CA3": "55c9856f-7824-48b2-992d-5112f06af292",
    "CA4": "b77a553d-e162-4b58-b0db-12563a4fd190"
  },
  "last_rotation": 1783189363,

  "health": {
    "kme_fail_count": 0,
    "kme_unavailable_since": 0,
    "last_kme_error": null,
    "degraded": false,
    "declared_down": false
  }
}
```

Quando `DEC` fallisce su `sae_003`:

```text
DEC FAILED
```

non devi fare MACsec down subito.

Devi fare:

```text
kme_fail_count += 1
if kme_unavailable_since == 0:
    kme_unavailable_since = now
degraded = true
save_state()
return False
```

E soprattutto:

```text
NON cambiare active_ca
NON incrementare generation
NON attivare una nuova CA
NON fare rebootstrap
NON fare macsec_down
```

***

# Policy temporale

Io farei così:

```python
KME_FAIL_THRESHOLD = 5
KME_HOLD_DOWN_SECONDS = 3600
```

Significato:

## Primo errore DEC/ENC

```text
mark degraded
keep current MACsec active CA
skip rotation
```

## Dopo 5 errori consecutivi

```text
KME considered unavailable
still keep MACsec up
still do not rotate
```

## Dopo 1 ora di KME indisponibile

```text
declare link down
macsec_down(iface)
declared_down = true
```

## Quando il KME torna

Al primo `ENC/DEC OK`:

```text
clear degraded
clear fail_count
clear kme_unavailable_since
clear declared_down
then allow bootstrap/rotation again
```

***

# Cambiamento più importante

Questa logica attuale è sbagliata/dannosa:

```python
if not key:
    if DEC_RETRY > 0:
        macsec_down(iface)
    return False
```

Nel tuo caso, se KME3 è giù, questa logica può buttare giù il MACsec anche se hai ancora una CA valida già attiva.

Deve diventare:

```python
if not key:
    record_kme_failure(peer, iface, state, link["ca_names"], "DEC_FAILED")
    print(f"ERROR DEC FAILED key_id={key_id}")
    return False
```

Niente `macsec_down()` lì.

***

# Molto importante: ordine corretto della rotazione

Il tuo flow attuale fa:

```text
activate standby
poi generate key
poi program old active
```

Questo è OK se entrambi i buffer sono già validi.

Però in failure mode devi garantire:

```text
se peer program fails
non toccare active CA successiva
non aggiornare state
```

Nel tuo caso specifico il problema maggiore sembra nel bootstrap/rebootstrap: il master prova a programmare entrambe le CA mentre KME3 è giù, fallisce DEC, ma poi tenta comunque activate.

Quindi anche in `bootstrap_link()` devi blindare:

```python
if not send_command(... "program" ...):
    log("BOOTSTRAP FAILED peer program CAx -> HOLD EXISTING MACSEC", ...)
    return False
```

E soprattutto `run_master()` non deve fare retry aggressivo ogni minuto con rebootstrap distruttivo.

***


# Comportamento finale desiderato

Con `kme3` down:

```text
07:41:25 sae_003 DEC failed
07:41:31 peer returns ERROR DEC FAILED
acx2 detects peer program failed
acx2 marks link acx2-acx3 degraded
acx2 keeps existing active_ca
acx2 does NOT rebootstrap
acx2 does NOT activate unprogrammed CA
acx2 does NOT macsec_down
traffic continues on last known good CA
```

Dopo 5 failure:

```text
KME considered unavailable
still hold active CA
```

Dopo 1 ora:

```text
KME HOLD EXPIRED
MACSEC DOWN
declared_down = true
```

Quando KME torna prima dell’ora:

```text
next DEC/ENC OK
clear failure state
resume normal double-buffer rotation
```

***

# Quindi sì: questo è il modello corretto

Quello che vuoi è una differenza netta tra:

## Failure temporaneo KME

```text
hold last good MACsec
degraded mode
no key rotation
no rebootstrap
```

## Failure persistente oltre policy

```text
declare link down
macsec_down
operator-visible hard failure
```

E soprattutto: **il master deve conoscere lo stato del peer**, quindi il peer deve salvare `health` nello state e il master deve leggerlo via `status`.

Questa è la prossima patch importante: `DEC FAILED` deve diventare uno stato link-level, non solo un log locale su `acx3`.





ACTIVATE OK
state active_ca updated
then verify macsec inuse

if not inuse immediately:
    do not panic
    mark temporary macsec_pending
    wait/retry for N seconds

if inuse appears:
    clear macsec_pending
    continue

if inuse does not appear after grace timer:
    if KME degraded:
        hold current CA
    else:
        controlled rebootstrap


Perché hai due stati diversi:
1. Transient after activation


CA name: CA4
no Status: inuse yet

Real operational failure

CA name: CA4
no Status: inuse for too long





grep '\[et-2/0/2\]' /var/tmp/qkd_debug.log
## eventi importanti
grep '\[et-2/0/2\]' /var/tmp/qkd_debug.log | grep -E "KME|HOLD|MACSEC|PEER PROGRAM|DEC|STATE|ACTIVE|BOOTSTRAP|MASTER DONE"
## solo KME down
grep '\[et-2/0/2\]' /var/tmp/qkd_debug.log | grep -E "KME FAILURE|KME HOLD ACTIVE|KME HOLD EXPIRED|MACSEC DOWN|KEEP CURRENT ACTIVE CA"
## solo kme/dec
grep '\[et-2/0/4\]' /var/tmp/qkd_debug.log | grep -E "DEC|KME|PROGRAM ABORTED|ACTIVE|STATE"

per seguire...su acx2
tail -f /var/tmp/qkd_debug.log | grep '\[et-2/0/2\]' | grep -E "KME|HOLD|MACSEC|PEER PROGRAM|DEC|STATE|ACTIVE|BOOTSTRAP"
su acx3: 
tail -f /var/tmp/qkd_debug.log | grep '\[et-2/0/4\]'


sh -c 'while true; do clear; date; echo "=== STATE ==="; cat /var/tmp/qkd_db_acx2_et-2_0_4.json; echo; echo "=== MACSEC ==="; cli -c "show security macsec connections | match \"Interface name|CA name|Status: inuse\""; sleep 2; done'




## testing di hub/spoke: 

python3 qkd_orchestrator.py create \
  --mode qkd \
  --topology hub \
  --hub acx1 \
  --devices acx1 acx2 acx5 \
  --ips 100.123.170.202 100.123.170.201 100.123.182.1 \
  --interfaces et-2/0/4,et-2/0/2 et-2/0/0 et-0/0/2 \
  --kmes 100.123.252.10 100.123.252.11 100.123.252.14 \
  --platform acx

  
(venv) root@qkd-01:~/new_qkd_dist# python3 - << 'EOF'
import yaml

with open("config/runtime/devices.yaml") as f:
    data = yaml.safe_load(f)

for name, dev in data["devices"].items():
    print()
    print("===", name, "===")
    print("sae:", dev.get("qkd", {}).get("sae_id"))
    print("interfaces:", dev.get("interfaces"))
    for l in dev.get("links", []):
        print(l)
EOF

=== acx1 ===
sae: sae_001
interfaces: None
{'peer': 'acx2', 'peer_ip': '100.123.170.201', 'peer_interface': 'et-2/0/0', 'peer_sae': 'sae_002', 'role': 'master', 'interface': 'et-2/0/4', 'ca_names': ['CA1', 'CA2']}
{'peer': 'acx5', 'peer_ip': '100.123.182.1', 'peer_interface': 'et-0/0/2', 'peer_sae': 'sae_003', 'role': 'master', 'interface': 'et-2/0/2', 'ca_names': ['CA3', 'CA4']}

=== acx2 ===
sae: sae_002
interfaces: None
{'peer': 'acx1', 'peer_ip': '100.123.170.202', 'peer_interface': 'et-2/0/4', 'peer_sae': 'sae_001', 'role': 'slave', 'interface': 'et-2/0/0', 'ca_names': ['CA1', 'CA2']}

=== acx5 ===
sae: sae_003
interfaces: None
{'peer': 'acx1', 'peer_ip': '100.123.170.202', 'peer_interface': 'et-2/0/2', 'peer_sae': 'sae_001', 'role': 'slave', 'interface': 'et-0/0/2', 'ca_names': ['CA3', 'CA4']}
(venv) root@qkd-01:~/new_qkd_dist# 




da shell (type sh in the start shell ...) 
cat > /var/tmp/ring_ping_rotation_test.sh << 'EOF'
#!/bin/sh

SRC="10.100.255.7"
DURATION=90
COUNT_PER_ROUND=5

DESTS="
acx2:10.100.255.9
acx3:10.100.255.8
acx4:10.100.255.11
acx5:10.100.255.10
"

START=$(date +%s)
END=$((START + DURATION))
ROUND=1

echo "============================================================"
echo " Ring MACsec/QKD ping rotation test"
echo " Source loopback: $SRC"
echo " Duration: ${DURATION}s"
echo " Ping count per destination per round: ${COUNT_PER_ROUND}"
echo " Start: $(date)"
echo "============================================================"
echo

echo "=== Initial MACsec state ==="
cli -c 'show security macsec connections | match "Interface name|CA name|Status: inuse"'
echo

echo "=== Initial routes from source node ==="
for item in $DESTS; do
    NAME=$(echo "$item" | cut -d: -f1)
    DST=$(echo "$item" | cut -d: -f2)
    echo "--- route to $NAME $DST ---"
    cli -c "show route $DST"
done

echo
echo "============================================================"
echo " Starting ping loop"
echo "============================================================"

while [ "$(date +%s)" -lt "$END" ]; do

    NOW=$(date '+%Y-%m-%d %H:%M:%S')
    echo
    echo "------------------------------------------------------------"
    echo "ROUND $ROUND at $NOW"
    echo "------------------------------------------------------------"

    for item in $DESTS; do
        NAME=$(echo "$item" | cut -d: -f1)
        DST=$(echo "$item" | cut -d: -f2)

        echo
        echo ">>> ping $NAME $DST source $SRC"

        cli -c "ping $DST source $SRC rapid count $COUNT_PER_ROUND"

        RC=$?

        if [ "$RC" -ne 0 ]; then
            echo "!!! PING COMMAND RETURNED NON-ZERO rc=$RC dst=$DST name=$NAME"
        fi
    done

    echo
    echo "=== MACsec state after round $ROUND ==="
    cli -c 'show security macsec connections | match "Interface name|CA name|Status: inuse"'

    ROUND=$((ROUND + 1))
    sleep 2

done

echo
echo "============================================================"
echo " Test completed at $(date)"
echo "============================================================"
echo

echo "=== Final MACsec state ==="
cli -c 'show security macsec connections | match "Interface name|CA name|Status: inuse"'

echo
echo "=== Final MACsec statistics summary ==="
cli -c 'show security macsec statistics | match "Interface name|Encrypted packets|Encrypted bytes|Accepted packets|Decrypted bytes"'
EOF


## problema con macsec ad LACP nel ring topology! 
* MACsec activate CAx
* LACP fast non riceve/valida PDU per pochi secondi
* LACP timeout
* AE down
* ISIS down
* MACsec torna inuse
* LACP torna collecting/distributing
* ISIS torna up

vedo : 
MACsec activate CAx
delete/set security macsec interface connectivity-association
dot1xd converge sulla nuova CA
LACP perde PDU per una finestra breve
LACP fast va in timeout
AE down
IS-IS down
poi MACsec torna in-use
LACP torna collecting/distributing
AE up
IS-IS up

posso ridurre il lacp down se: 
* "Non ruoto in condizioni instabili"
* "Non ruoto più link insieme" --> introduco un offset! 
* "Non faccio refresh immediato dopo activate"
* "Non causo rebootstrapping inutile"
* "Riduco le finestre di churn MACsec"

MIN_ROTATION_INTERVAL = 300


Post-activate settle delay
Dopo:
ACTIVATE OK
MACSEC INUSE CONFIRMED

non fare subito:
PROGRAM inactive_ca

Aspetta qualche secondo:
POST_ACTIVATE_SETTLE_SECONDS = 5

Perché nei log vedi spesso:
activate CAx
MACsec primary in-use
subito dopo program altra CA

Questo genera ulteriore churn su dot1xd/mgd.

prima di fare activate lo script deve fare show lacp interfaces <member> se e'configurato! 
Current
Collecting distributing
Syn Yes
Col Yes
Dist Yes

se non e'stabile ROTATION SKIP reason=LACP_NOT_STABLE
Non ruotare se il link ha appena recuperato MACsec/LACP
LINK_SETTLE_MIN_AGE_SECONDS = 30
Se la CA è appena diventata inuse, non fare altre operazioni su quel link per 30 secondi.



We reached the point where we understand if we get CAx and CAy and we apply at some stage delete intf CAx and set intf CAy we loose the control plane on the intf becaseu we reapply the config to the intf. ... this way lacp might go down and macsec with it ... so we must use key chains instead os separate CAx CAy for single link....



1. master gets new QKD key from KME
2. master sends key_id to peer
3. peer does DEC using key_id
4. both sides install the new key into the keychain
5. keychain/MKA handles rollover
6. script verifies new key/MKA/MACsec state
7. old key is cleaned later
`

pair example deploy: 
python3 qkd_orchestrator.py create --mode qkd --topology pair --devices  acx1 acx2 --ips 100.123.170.202 100.123.170.201 --interfaces et-2/0/4 et-2/0/0 --kmes 100.123.252.10 100.123.252.11




# PKI 
1. KME PKI

KME Root CA self-signed
KME Issuing/Sub CA
KME leaf certs: kme_001, kme_002, ecc.

2. Juniper PKI

Juniper Root CA self-signed
Juniper Issuing/Sub CA
Juniper device certs: acx7348-p1, vqfx-1, ecc.

3. Certificate chains

ogni leaf viene firmato dalla sua issuing CA
ogni endpoint presenta leaf + issuing chain

4. Trust exchange

lato Juniper installi/trusti: kme-issuing-ca.crt + kme-root-ca.crt
lato KME installi/trusti: juniper-issuing-ca.crt + juniper-root-ca.crt

Importante: non scambiare mai le private key delle CA. Solo .crt.



When i have dual PKI: on acx1: 
[vrf:none] admin@acx7348-p1-re0:/var/db/scripts/certs_dual_pki$ ls -lart 
total 44
drwxrwx--x  14 root  root  4096 Jul  9 08:11 ..
-rw-r--r--.  1 admin admin 4033 Jul  9 08:15 trusted-kme-ca-bundle.crt
-rw-r--r--.  1 admin admin 2110 Jul  9 08:15 sae_001.crt
-rw-------.  1 admin admin 3243 Jul  9 08:15 sae_001.key
-rw-r--r--.  1 root  root  2130 Jul  9 08:27 kme_001.crt
drwxr-xr-x.  2 admin admin 4096 Jul  9 08:27 .
[vrf:none] admin@acx7348-p1-re0:/var/db/scripts/certs_dual_pki$ openssl verify -CAfile trusted-kme-ca-bundle.crt kme_001.crt
kme_001.crt: OK

I can remove kme_001.crt from acx1, it was only for testing. 


Start a parallel docker container to check the dual_pki...
```sh
docker run -d \
  --name aterren-kme01-dual \
  --restart unless-stopped \
  --network qkd_net \
  -p 8444:8443 \
  -v /home/aterren/kme-lab/etsi-gs-qkd-014-referenceimplementation/certs_dual_pki:/certs:ro \
  -e ETSI_014_REF_IMPL_TLS_PRIVATE_KEY=/certs/kme_001.key \
  -e ETSI_014_REF_IMPL_TLS_CERT=/certs/kme_001.crt \
  -e ETSI_014_REF_IMPL_TLS_ROOT_CRT=/certs/root.crt \
  -e ETSI_014_REF_IMPL_DB_URL="postgres://db_user:db_password@100.123.252.30:5432/key_store" \
  -e ETSI_014_REF_IMPL_IP_ADDR=0.0.0.0 \
  -e ETSI_014_REF_IMPL_PORT_NUM=8443 \
  -e ETSI_014_REF_IMPL_NUM_WORKER_THREADS=2 \
  aterren-etsi-kme:local
```


```bash
[vrf:none] admin@acx7348-p1-re0:/var/db/scripts/certs_dual_pki$ curl -vk --cert sae_001.crt --key sae_001.key --cacert trusted-kme-ca-bundle.crt "https://100.123.252.99:8443"
```

```bash
python3  -m compileall lib *
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -type f -name "*.pyc" -delete
```

