# Flusso operativo `ver3.3.4.1`: QKD on-box, RPC e rotazione chiavi

## 1. Scopo

Questo documento descrive, passo per passo, cosa esegue il branch
`ver3.3.4.1` su ogni router:

1. avvio periodico di `qkd_onbox.py`;
2. selezione del ruolo master o slave per ciascun link;
3. acquisizione e installazione batch delle chiavi QKD/MACsec;
4. trasporto sicuro dei soli Key ID tramite RPC SSH;
5. acknowledgement sincrono e recupero delle transazioni interrotte;
6. rotazione transazionale della chiave SSH usata dalle RPC tra router.

Il modello corrente è **RPC-only**. Non usa `etsi_peer_view`, code di file,
SCP tra router o polling di file ACK.

---

## 2. Componenti e identità

### 2.1 Processo runtime

Il runtime installato su ogni router è:

```text
/var/db/scripts/op/qkd_onbox.py
/var/db/scripts/event/qkd_onbox.py
```

Junos `event-options` genera l'evento `QKD_TIMER` ogni
`execution_interval_seconds` e avvia lo script come:

```text
python-script-user etsi_user
```

Il valore predefinito del branch è:

```yaml
execution_interval_seconds: 60
```

Lo script rifiuta l'esecuzione come `root` o come un utente diverso da
`etsi_user`.

### 2.2 Identità coinvolte

| Identità | Dove viene usata | Funzione |
|---|---|---|
| bootstrap/install user | orchestratore Linux verso router | controlli privilegiati e installazione |
| upload user | orchestratore Linux verso `/var/tmp` | caricamento degli artefatti durante il deploy |
| `etsi_user` | runtime su ogni router | esecuzione event/op script e RPC router-to-router |
| `qkd_id_ed25519` | orchestratore Linux verso `etsi_user` | gestione e validazione |
| `qkd_rpc_id_ed25519` | router verso router | RPC runtime per stato, batch e rotazione identità |

La chiave privata RPC di un router rimane sul router che la possiede.

---

## 3. Che cosa significa RPC in `ver3.3.4.1`

RPC significa **Remote Procedure Call**: un router richiede a un peer di
eseguire una funzione applicativa ben definita e riceve direttamente il suo
risultato.

In questo branch RPC non significa:

- server HTTP;
- REST `POST /install_key`;
- gRPC;
- NETCONF usato direttamente tra i due runtime;
- copia di un file da elaborare in seguito.

È una RPC applicativa sincrona trasportata su SSH. Il chiamante apre una
sessione SSH autenticata come `etsi_user` e invoca l'op-script Junos:

```text
op qkd_onbox.py action <azione> <parametri>
```

Esempio:

```text
Router A
  |
  | SSH autenticato con qkd_rpc_id_ed25519
  v
Router B: op qkd_onbox.py action install-key-batch ...
  |
  +--> valida richiesta
  +--> esegue DEC verso il proprio KME
  +--> committa la keychain
  +--> restituisce output + exit code
```

Il confine RPC è quindi composto da:

1. **trasporto:** SSH;
2. **identità:** `etsi_user` + chiave `qkd_rpc_id_ed25519`;
3. **metodo:** valore di `action`;
4. **argomenti:** interfaccia, payload batch, device sorgente o public key;
5. **risposta:** stdout/stderr ed exit code del processo remoto;
6. **timeout:** limite imposto dal chiamante;
7. **mutua consistenza:** stato persistente locale e verifica live del peer.

### 3.1 RPC applicativa e RPC NETCONF

L'orchestratore off-box usa PyEZ/NETCONF per deploy e configurazione. Il
runtime router-to-router usa invece SSH per lanciare un'op-script Junos.

Entrambi possono essere descritti genericamente come chiamate remote, ma sono
due piani distinti:

| Piano | Chiamante | Destinatario | Meccanismo |
|---|---|---|---|
| deploy/configurazione | Linux orchestrator | router | PyEZ/NETCONF e SCP |
| runtime SAE-to-SAE | router | peer router | SSH + `op qkd_onbox.py action ...` |

### 3.2 Semantica sincrona

La chiamata rimane aperta finché il peer:

- completa l'operazione;
- restituisce successo;
- restituisce errore;
- oppure supera il timeout.

Per `install-key-batch`, il solo completamento del trasporto SSH non basta. Il
master richiede anche:

```text
return code = 0
output contiene "OK INSTALL-KEY-BATCH"
output non contiene marker di errore
```

Questo rende la risposta una conferma applicativa, non soltanto una conferma
di consegna dei byte.

---

## 4. Perché RPC ha sostituito SCP nel runtime router-to-router

### 4.1 Limite concettuale di SCP

SCP trasferisce un file. Non esprime direttamente:

- quale operazione applicativa deve essere eseguita;
- quando il peer ha validato il contenuto;
- quando il peer ha completato DEC dal KME;
- quando la keychain è stata committata;
- perché l'operazione è fallita;
- quale stato applicativo possiede ora il peer.

Nel modello precedente servivano quindi componenti aggiuntivi:

```text
crea file
  -> SCP
  -> directory inbox
  -> polling
  -> parsing file
  -> installazione
  -> file ACK
  -> polling ACK
  -> cleanup
```

Una copia SCP riuscita dimostrava soltanto che il file era arrivato. Non
dimostrava che la chiave fosse stata ottenuta dal KME e installata in Junos.

### 4.2 Problemi eliminati

Il passaggio a RPC elimina dal runtime:

- `etsi_peer_view`;
- directory inbox/outbox;
- file batch condivisi;
- file ACK;
- polling periodico;
- cleanup dei file consumati;
- rischio di rileggere file vecchi;
- ambiguità tra consegna del file e applicazione della richiesta;
- transport mode `queue`;
- fallback silenziosi fra SCP e RPC.

### 4.3 Vantaggi della RPC SSH

#### Risposta applicativa immediata

Il master sa nello stesso comando se il peer ha:

- decodificato il payload;
- eseguito DEC;
- committato il batch;
- salvato lo stato.

#### Errori espliciti

Il peer può restituire una causa precisa:

```text
DEC_FAILED
KEYCHAIN_BATCH_INSTALL_FAILED
STATE_SAVE_FAILED
INTERFACE_BIND_FAILED
```

Il master conserva l'inflight e non presenta il fallimento come successo.

#### Stato live

L'azione `status` permette di confrontare direttamente:

- active Key ID;
- pending Key ID;
- active e next slot;
- metadati del ring;
- configurazione effettiva.

Con SCP sarebbe necessario produrre, trasferire e invalidare snapshot di stato.

#### Minor superficie operativa

Un solo utente runtime e una sola famiglia di operazioni sostituiscono account,
directory e script dedicati al trasferimento file.

#### Recupero deterministico

La transazione è persistita sul master. Dopo un riavvio:

- il master interroga il peer;
- finalizza se il peer ha già applicato il batch;
- ritenta se il batch è ancora valido;
- lo abbandona se lo start-time è ormai scaduto.

Non deve dedurre lo stato dalla presenza o assenza di file.

#### Sicurezza del trasporto

SSH fornisce:

- autenticazione del router sorgente;
- cifratura del canale;
- integrità della richiesta e della risposta;
- autorizzazione tramite public key in Junos;
- rotazione indipendente dell'identità RPC.

Il payload continua comunque a non contenere il key material QKD.

### 4.4 Perché non è stato scelto HTTP/gRPC

L'op-script Junos e SSH erano già disponibili sulle piattaforme target. La
scelta evita di introdurre:

- un daemon HTTP aggiuntivo;
- una porta di ascolto applicativa;
- lifecycle e supervisione di un altro servizio;
- librerie server non necessariamente presenti su Junos;
- un secondo modello di autenticazione.

La semantica è RPC, mentre il trasporto sfrutta il sottosistema SSH già
supportato e amministrabile su Junos.

### 4.5 Dove SCP rimane legittimo

SCP non è stato eliminato dal deploy off-box. L'orchestratore Linux può ancora
usarlo per caricare in `/var/tmp`:

- script;
- JSON runtime;
- certificati.

Questa è una distribuzione amministrativa di artefatti, non coordinazione
runtime SAE-to-SAE. Dopo l'upload, un'identità privilegiata installa i file
nelle directory Junos definitive.

La distinzione è:

```text
SCP: distribuzione di file dal sistema di gestione
RPC: esecuzione sincrona di operazioni tra runtime peer
```

---

## 5. Configurazione per-device

Il comando `create` genera per ogni device:

```text
config/runtime/<device>/qkd_onbox.py
config/runtime/<device>/qkd_onbox_config.json
config/runtime/<device>/qkd_onbox_inventory.json
```

I JSON includono la vista locale del device:

- nome e SAE ID locale;
- KME e certificati ETSI 014;
- interfacce e peer diretti;
- ruolo `master` o `slave` per ciascun link;
- CA e keychain MACsec;
- policy di batch, intervalli, timeout e strict sync.

Lo stesso sorgente Python viene quindi eseguito con una configurazione diversa
su ciascun router.

---

## 6. Avvio di un ciclo periodico

Quando `qkd_onbox.py` parte senza un'azione RPC:

1. registra versione e avvio;
2. verifica che l'utente runtime sia `etsi_user`;
3. controlla e corregge i permessi dei file runtime consentiti;
4. verifica che il modello MACsec sia `keychain`;
5. aggiorna gli snapshot diagnostici dello stato peer;
6. verifica leggibilità della chiave RPC e dei certificati KME;
7. acquisisce il lock globale del processo;
8. esegue `run_master()`;
9. rilascia il lock globale.

Se lo script riceve un'azione RPC, esegue invece soltanto l'handler richiesto e
termina con exit code `0` o `1`.

Azioni RPC correnti:

```text
status
install-key-batch
prepare-rpc-pubkey
finalize-rpc-pubkey
```

---

## 7. Ruolo master e ruolo slave

Il ruolo è assegnato **per link**, non globalmente al router. Lo stesso device
può essere master su un link e slave su un altro.

### Master

Il master:

- decide se è sicuro iniziare una nuova transazione;
- richiede nuove chiavi al proprio KME con ETSI 014 `GetKey`/ENC;
- prepara il batch con Key ID, slot e start-time;
- installa localmente chiavi e metadati;
- invia al peer soltanto i Key ID e i metadati;
- attende l'esito sincrono del peer;
- conserva lo stato inflight fino alla conferma bilaterale.

### Slave

Lo slave non riceve la chiave QKD dal master. Riceve i Key ID e:

- usa ogni Key ID per richiedere la chiave al proprio KME con
  `GetKeyWithKeyIDs`/DEC;
- installa il batch nella propria keychain;
- salva lo stato;
- restituisce successo o errore nello stesso comando RPC.

---

## 8. Controlli eseguiti dal master prima della rotazione

Per ogni link con ruolo master, `run_master_rolling_link()`:

1. carica il DB di stato del link;
2. adotta il seed iniziale creato dall'orchestratore, se necessario;
3. riconcilia stato software e configurazione Junos;
4. promuove una pending key solo se confermata da evidenza MKA;
5. verifica che la configurazione locale sia valida;
6. se esiste una transazione inflight, tenta prima di completarla;
7. verifica che MACsec abbia una SA `in-use`;
8. richiede al peer lo stato live con RPC `status`;
9. confronta active Key ID e active slot;
10. confronta insieme degli slot, metadati e next pending slot; il next
    pending slot locale viene ricalcolato all'istante `status_epoch`
    pubblicato dal peer, così una start-time che scade tra le due letture non
    produce un falso `NEXT_KEY_NOT_BILATERALLY_CONFIRMED`;
11. se lo slot successivo all'active ha già raggiunto la start-time ma MKA non
    lo ha ancora confermato (`next_slot == active_slot + 2`), registra
    `ROTATION DEFER reason=KEY_TRANSITION_IN_PROGRESS` (INFO) e rimanda al
    ciclo successivo invece di bloccare con
    `ACTIVE_PENDING_PAIR_NOT_ADJACENT`;
12. seleziona gli slot sostituibili senza toccare active e next;
13. applica intervallo minimo, grace adattivo e margine di attivazione;
14. procede solo se `rekey_enabled` è attivo.

Con `strict_sync_enabled: true`, qualsiasi divergenza blocca la creazione di
un nuovo batch. Il runtime privilegia la consistenza bilaterale rispetto
all'avanzamento forzato.

---

## 9. Creazione del batch QKD sul master

Il ring predefinito contiene quattro slot:

```yaml
key_batch_size: 4
max_installed_keys: 4
key_activation_interval_seconds: 300
```

Il numero di chiavi generate in un ciclo corrisponde agli slot selezionati:

- bootstrap/rearm può riempire più slot;
- steady state sostituisce soltanto gli slot `N-2` già consumati;
- active e next rimangono protetti.

Per ogni slot selezionato il master:

1. calcola `generation`;
2. chiama il proprio KME in modalità ENC;
3. riceve `key_id` e chiave QKD;
4. calcola lo `start_time`;
5. crea un record locale completo.

Record locale:

```json
{
  "generation": 42,
  "slot": 2,
  "start_time": "2026-09-28.16:30:00 +0200",
  "key_id": "<ETSI-Key-ID>",
  "key": "<QKD-key-material>"
}
```

Prima della RPC, il master rimuove il campo `key`.

Payload inviato al peer:

```json
[
  {
    "generation": 42,
    "slot": 2,
    "start_time": "2026-09-28.16:30:00 +0200",
    "key_id": "<ETSI-Key-ID>"
  }
]
```

**La chiave QKD/CAK non attraversa mai il collegamento SSH.** Attraversano
soltanto Key ID e metadati di coordinamento.

---

## 10. Persistenza inflight e commit locale

Prima di modificare Junos, il master salva nel DB:

```text
operation
records senza key material
payload_b64
ack_id
created_at
timestamp di ENC/commit/invio
```

Questo stato `inflight_install` rende la transazione recuperabile dopo:

- riavvio dello script;
- timeout SSH;
- lock del database Junos;
- perdita temporanea del peer;
- risposta RPC non ricevuta.

Dopo aver persistito l'inflight:

1. il master installa il batch nella keychain locale;
2. committa la configurazione;
3. aggiorna il timestamp di fine commit;
4. invia il batch al peer.

Se il commit locale non esiste realmente, l'inflight viene eliminato. Se la
configurazione è presente ma la verifica ha avuto un problema, l'inflight
rimane per consentire il recupero.

---

## 11. RPC `install-key-batch`

Il master esegue:

```text
ssh -i /var/home/etsi_user/.ssh/qkd_rpc_id_ed25519 \
  -o IdentitiesOnly=yes \
  etsi_user@<peer-ip> \
  "op qkd_onbox.py action install-key-batch \
   iface <peer-interface> batch-b64 <payload>"
```

Prima dell'invio verifica che il primo start-time conservi almeno:

```yaml
peer_enqueue_min_margin_seconds: 60
```

La chiamata batch ha timeout sincrono:

```yaml
peer_batch_ack_timeout_seconds: 150
```

L'RPC è considerata riuscita solo se:

- SSH termina con return code `0`;
- l'output non contiene marker di errore;
- l'output contiene `OK INSTALL-KEY-BATCH`.

Non esistono file ACK o polling successivi per determinare l'esito immediato
della chiamata.

---

## 12. Elaborazione del batch sul peer

Il peer:

1. acquisisce un action lock per interfaccia;
2. decodifica il payload Base64 URL-safe;
3. valida lista, Key ID, generation, slot e start-time;
4. per ciascun Key ID chiama il proprio KME in modalità DEC;
5. ottiene localmente il corrispondente key material;
6. prepara tutte le entry di installazione;
7. installa e committa il batch nella keychain;
8. collega l'interfaccia alla CA/keychain stabile;
9. aggiorna pending queue, generazioni e ring state;
10. riconcilia configurazione e stato;
11. promuove solo una chiave confermata da MKA;
12. persiste il DB;
13. stampa `OK INSTALL-KEY-BATCH count=<n>`;
14. termina con exit code `0`.

Se un singolo DEC o il commit batch falliscono, l'intera RPC fallisce e il
master non finalizza la transazione.

---

## 13. ACK, finalizzazione e strict sync

La risposta del comando remoto è l'ACK sincrono.

Dopo ACK positivo il master:

1. registra le misure ENC, commit, invio e risposta;
2. finalizza nel DB gli stessi record installati;
3. riconcilia lo stato locale;
4. salva lo stato senza `inflight_install`;
5. richiede nuovamente `status` al peer;
6. verifica che i due ring siano coerenti.

L'attivazione effettiva della chiave non viene dedotta dal solo commit. Nei
cicli successivi `promote_pending_key_if_mka_confirmed()` usa l'evidenza MKA
per aggiornare active e previous-active.

---

## 14. Recupero di una transazione inflight

Quando il ciclo successivo trova `inflight_install`:

1. verifica che i record siano ancora presenti nella configurazione locale;
2. interroga lo stato live del peer;
3. se il peer contiene già gli stessi record, finalizza senza reinvio;
4. altrimenti ritenta la stessa RPC finché il batch è ancora temporalmente
   valido;
5. se l'età supera `inflight_stuck_seconds` (default 600), abbandona il batch
   scaduto e persiste il reset;
6. il ciclo seguente può creare un batch nuovo con start-time valido.

Marker del recupero automatico:

```text
INFLIGHT STUCK ... action=AUTO_RING_RESET
INFLIGHT ABANDONED ... reason=STUCK_TIMEOUT_EXCEEDED
```

Se il salvataggio del reset fallisce, la transazione originale viene
ripristinata in memoria e la rotazione rimane bloccata.

---

## 15. Rotazione della chiave RPC SSH

La rotazione della chiave RPC è indipendente dalla rotazione QKD/MACsec.

Valore predefinito del branch:

```yaml
rpc_key_rotation_interval_seconds: 600
```

`run_master()` completa prima tutto il lavoro MACsec. Solo dopo verifica se
deve avviare o riprendere la rotazione di `qkd_rpc_id_ed25519`. In questo modo
l'identità SSH non cambia nel mezzo di una transazione keyring.

La rotazione è considerata scaduta quando mancano meno di metà
`execution_interval_seconds` (30 s con il timer a 60 s) ai 600 s. Il
`last_rotation_timestamp` viene infatti scritto a fine ciclo, qualche secondo
dopo il tick che l'ha avviata: senza tolleranza il tick dopo 10 minuti troverebbe
ancora `next_rotation_in_seconds=7` e la rotazione slitterebbe a 11 minuti.

### 15.1 Generate

Il router genera:

```text
qkd_rpc_id_ed25519.next
qkd_rpc_id_ed25519.next.pub
```

e salva una transazione in:

```text
/var/home/etsi_user/qkd_rpc_key_rotation.json
```

La transazione contiene:

- ID;
- fase corrente;
- nuova public key;
- insieme completo dei peer diretti;
- peer prepared;
- peer verified;
- flag activated;
- peer finalized;
- timestamp di creazione.

### 15.2 Prepare

Usando ancora la chiave RPC attiva, il router invia a ogni peer:

```text
op qkd_onbox.py action prepare-rpc-pubkey \
  device <source-device> pubkey-b64 <new-public-key>
```

Ogni peer aggiunge la nuova chiave alla configurazione Junos di `etsi_user`
con commento:

```text
qkd-rpc@<source-device>
```

La chiave vecchia resta autorizzata: non si interrompono le RPC correnti.

### 15.3 Verify

Il router usa fisicamente `qkd_rpc_id_ed25519.next` per eseguire `status`
contro ogni peer diretto.

L'attivazione locale è vietata finché tutti i peer non risultano:

- prepared;
- raggiungibili con la chiave `.next`.

### 15.4 Activate

Dopo verifica completa:

1. la chiave attiva viene copiata in `.prev`;
2. `.next` sostituisce atomicamente `qkd_rpc_id_ed25519`;
3. `.next.pub` sostituisce la public key attiva;
4. la transazione viene salvata come `activated`.

La private key nuova non viene mai trasferita.

### 15.5 Finalize

Usando la nuova chiave attiva, il router chiama su ogni peer:

```text
op qkd_onbox.py action finalize-rpc-pubkey \
  device <source-device> pubkey-b64 <new-public-key>
```

Ogni peer:

- conserva la nuova chiave;
- elimina soltanto le chiavi precedenti con lo stesso tag sorgente;
- non modifica le chiavi RPC degli altri router;
- non modifica la chiave dell'orchestratore Linux.

Quando tutti i peer sono finalizzati:

- incrementa `rotation_count`;
- aggiorna `last_rotation_timestamp`;
- elimina la transazione pendente.

Un riavvio riprende dalla fase salvata senza rigenerare o saltare fasi.

---

## 16. Relazione tra le due rotazioni

| Aspetto | Rotazione QKD/MACsec | Rotazione RPC SSH |
|---|---|---|
| Oggetto | chiavi simmetriche usate da MACsec | identità ED25519 del router |
| Sorgente | KME ETSI 014 | generazione locale sul router |
| Dato inviato al peer | Key ID, slot, generation, start-time | nuova public key |
| Private/secret key trasferita | no | no |
| Frequenza predefinita | start-time ogni 300 s | ogni 600 s |
| Conferma | ACK RPC + stato peer + MKA | prepare + verify + activate + finalize |
| Stato persistente | DB per link con inflight | DB transazione RPC per device |

La RPC SSH è il canale di coordinamento. Le chiavi QKD restano indipendenti sui
due KME, mentre i Key ID consentono ai due SAE di ottenere lo stesso materiale
senza trasmetterlo tra router.

---

## 17. File di stato e lock principali

Percorsi predefiniti:

```text
/var/home/etsi_user/qkd_db_<peer>_<iface>.json
/var/home/etsi_user/qkd_rpc_key_rotation.json
/var/home/etsi_user/qkd_onbox_<local-sae>.lock
/var/home/etsi_user/qkd_onbox_<local-sae>_<iface>_<action>.lock
/var/home/etsi_user/qkd_junos_commit.lock
/var/home/etsi_user/logs/qkd_debug.log
```

Il lock globale impedisce due cicli master contemporanei. Gli action lock
serializzano le RPC concorrenti. Il Junos commit lock serializza tutte le
operazioni che modificano la configurazione.

---

## 18. Marker operativi utili

Rotazione QKD/MACsec:

```text
MASTER START
ROLLING_REPLACEMENT START
RING_REARM START
SENDING KEY-ID BATCH TO PEER ... count=N slots=... key_ids=...
OK INSTALL-KEY-BATCH
INFLIGHT FINALIZED
ROLLING_REPLACEMENT DONE
ROTATION BLOCKED reason=...
```

Rotazione identità RPC:

```text
RPC-KEY-STATE
RPC-KEY ROTATION START
OK PREPARE-RPC-PUBKEY
RPC-KEY VERIFY
OK FINALIZE-RPC-PUBKEY
RPC KEY ROTATION COMPLETED
```

---

## 19. Riassunto end-to-end

```text
Junos QKD_TIMER
  |
  v
qkd_onbox.py come etsi_user su ogni router
  |
  +--> action RPC ricevuta?
  |      +--> status / install-key-batch / prepare / finalize
  |
  +--> ciclo master per ogni link dove role=master
         |
         +--> strict-sync e verifica MKA/ring
         +--> ENC dal KME locale
         +--> commit batch locale
         +--> SSH RPC con soli Key ID
         |      |
         |      +--> peer DEC dal proprio KME
         |      +--> peer commit batch
         |      +--> ACK sincrono
         |
         +--> finalizzazione inflight e verifica bilaterale
         |
         +--> dopo tutto il lavoro MACsec:
                rotate qkd_rpc_id_ed25519
                generate -> prepare -> verify -> activate -> finalize
```

Il risultato è una rotazione MACsec coordinata senza trasferimento del
materiale QKD e con una seconda rotazione indipendente dell'identità SSH che
protegge le RPC router-to-router.

---

## 20. Lettura di `qkd_debug.log`: sequenza reale EVO1 ↔ EVO2

Esempio ricavato dai log di EVO1 (`sae-001`, master su `et-0/0/1`) ed EVO2
(`sae-002`, slave) del 2026-09-28, ora router PDT. Le start-time nei marker
batch sono in UTC (`16:43:04 +0000` = `09:43:04 PDT`). Log:
`/var/home/etsi_user/logs/qkd_debug.log`; stato RPC:
`/var/home/etsi_user/qkd_rpc_key_rotation.json`.

Ring di 4 slot, una nuova chiave MACsec entra in uso ogni 5 minuti (secondo
`:04`), batch di sostituzione ogni ~10 minuti.

### 20.1 Dopo un deploy: reset al seed e `RING_COMPLETION`

Ogni `deploy` riscrive la keychain con il solo seed dell'orchestratore
(slot 0). Il primo ciclo successivo riconosce il reset e ripopola gli slot
1-3 con chiavi QKD:

| Ora | Router | Marker | Significato |
|---|---|---|---|
| 09:40:01 | EVO1 | `ORCHESTRATOR SEED RESET RECONCILED ... new_active_key_id=QKD_CA_EVO1_EVO2:bootstrap:key-name:0` | stato locale riallineato al seed del deploy |
| 09:40:03 | EVO2 | `ORCHESTRATOR SEED RESET RECONCILED` | idem sul peer |
| 09:40:04 | EVO1 | `RING_COMPLETION START slots=[1, 2, 3] active_slot=0 first_start_time=... 16:43:04 +0000` | ENC dal KME, commit locale di 3 chiavi |
| 09:40:06 | EVO2 | `INSTALL-KEY-BATCH REQUEST count=3` | RPC dal master con i soli Key ID; DEC dal KME e commit |
| 09:40:09 | EVO1 | `RING_COMPLETION DONE key_count=3 ring_phase=ready` | ACK ricevuto, verifica post-commit OK |

`RING_COMPLETION` dopo ogni deploy è quindi atteso, non un errore.

### 20.2 Regime: attivazioni e `ROLLING_REPLACEMENT`

| Ora | Router | Marker | Significato |
|---|---|---|---|
| 09:43:04 | EVO1 | `ROTATION DEFER reason=KEY_TRANSITION_IN_PROGRESS active_slot=0 starting_slot=1` | slot 1 entra in uso in questo secondo; MKA non lo ha ancora confermato, si attende il ciclo successivo |
| 09:48:04 | EVO1 | `ROTATION DEFER ... active_slot=1 starting_slot=2` | idem per lo slot 2 |
| 09:49:04 | EVO1 | `ROLLING_REPLACEMENT START slots=[0, 1] active_slot=2 next_slot=3 first_start_time=... 16:58:04 +0000` | gli slot già consumati (0, 1) vengono sostituiti; active e next non vengono toccati |
| 09:49:06 | EVO2 | `INSTALL-KEY-BATCH REQUEST count=2` | il peer installa le stesse 2 chiavi; elimina solo le pending degli slot sovrascritti |
| 09:49:08 | EVO1 | `ROLLING_REPLACEMENT DONE slots=[0, 1] key_count=2 ring_phase=ready` | batch bilaterale completato |

Nelle build fino a `da98585` inclusa, gli istanti `hh:mm:04` producevano invece
`ROTATION BLOCKED reason=NEXT_KEY_NOT_BILATERALLY_CONFIRMED` oppure
`ROTATION BLOCKED reason=ACTIVE_PENDING_PAIR_NOT_ADJACENT` (ERROR): erano la
stessa finestra di transizione letta da un solo lato o prima della conferma
MKA; costavano un ciclo senza compromettere la sessione. Anche
`ROTATION SKIP reason=N_MINUS_TWO_TARGETS_NOT_CONSUMED` (INFO) è normale:
significa che gli slot da sostituire non sono ancora stati consumati.

### 20.3 Rotazione della chiave SSH RPC (ogni 600 s)

Ogni router ruota la propria `qkd_rpc_id_ed25519` verso tutti i peer diretti.
Il marker `source_device=X` sul router ricevente indica il router che sta
pubblicando la sua nuova chiave.

| Ora | Router | Marker | Significato |
|---|---|---|---|
| 09:40:11 | EVO2 | `OK PREPARE-RPC-PUBKEY source_device=EVO1` | EVO1 aggiunge la sua nuova pubkey accanto a quella attiva |
| 09:40:16 | EVO2 | `OK FINALIZE-RPC-PUBKEY source_device=EVO1` | dopo verify e activate, EVO1 rimuove la vecchia pubkey |
| 09:40:18 | EVO1 | `RPC KEY ROTATION COMPLETED rotation_count=4` | transazione chiusa, `transaction: null` |
| 09:41:21 | EVO1 | `OK PREPARE-RPC-PUBKEY source_device=EVO2` | EVO2 prepara la sua nuova pubkey su EVO1 |
| 09:41:26 | EVO1 | `OK FINALIZE-RPC-PUBKEY source_device=EVO2` | vecchia pubkey di EVO2 rimossa |
| 09:41:27 | EVO2 | `RPC KEY ROTATION COMPLETED rotation_count=4` | rotazione EVO2 completata |

Storico del giorno: EVO1 completa alle 08:29, 08:40, 09:29, 09:40; EVO2 alle
08:30, 08:41, 09:30, 09:41. Il buco 08:51-09:28 non riguarda il link
EVO1-EVO2: una transazione include tutti i peer diretti ed è rimasta aperta
perché MX1 (per EVO1) e MX4 (per EVO2) rifiutavano `etsi_user`
(`RPC-KEY PREPARE-RPC-PUBKEY FAIL ... Permission denied`, seguito da
`RPC KEY ROTATION NOT COMPLETED this cycle -> will retry next cycle`). Dopo
il deploy delle 09:26 con `authorized_keys` corretto sugli MX:

| Ora | Router | Marker | Significato |
|---|---|---|---|
| 09:27:11 / 09:28:06 | EVO1 | `RPC-KEY VERIFY FAIL peer=EVO2 action=keep_current_key_and_reprepare` | il deploy aveva ripubblicato la chiave attiva sopra la `.next`; la chiave corrente resta valida |
| 09:28:20 / 09:29:21 | EVO2 | `RPC-KEY VERIFY FAIL peer=EVO1 action=keep_current_key_and_reprepare` | idem in direzione opposta |
| 09:29:10 | EVO1 | `RPC KEY ROTATION COMPLETED rotation_count=3` | recuperata dopo il re-prepare automatico |
| 09:30:25 | EVO2 | `RPC KEY ROTATION COMPLETED rotation_count=3` | recuperata |

`VERIFY FAIL ... keep_current_key_and_reprepare` subito dopo un deploy è
quindi un recupero atteso; se persiste per più cicli indica un problema di
`authorized_keys` sul peer.

Nota sulla concorrenza deploy / rotazione on-box: la fase di deploy
`SCRIPT_USER_RPC_KEYS` è solo additiva (aggiunge la chiave corrente di ogni
peer, non cancella nulla), e `FINALIZE-RPC-PUBKEY` riafferma sempre la nuova
chiave mentre rimuove le vecchie. Prima di questa correzione un deploy che
cadeva tra prepare e finalize di un peer (osservato su MX1 alle 09:40:12-18)
cancellava la nuova chiave di EVO1, e il finalize successivo cancellava la
vecchia: EVO1 restava senza chiavi su MX1 (`Permission denied`) fino al
deploy successivo.

### 20.4 Marker da ignorare per il link EVO1-EVO2

- `[et-0/0/2] ROTATION BLOCKED reason=MACSEC_NOT_INUSE`: riguarda il link
  EVO1-MX1, non EVO1-EVO2.
- `MKA_PARSE CAK LENGTH INVALID len=62` (WARN): artefatto di parsing del
  nome CAK, non bloccante.
- `MKA KEY NOT CONFIRMED ... ckn_match=False` subito dopo un batch: la
  pending key non è ancora in uso; diventa active solo con evidenza MKA.
