# Guida alla lettura dei log runtime QKD

Questa guida spiega cosa indicano i tag e le famiglie di messaggi del runtime
on-box. Non contiene copie di log: per l'analisi si consultano i file presenti
sul router o gli snapshot raccolti.

## Com'è fatta una riga

Il formato corrente è:

```text
TIMESTAMP [SEVERITY] [CONTESTO][SAE_LOCALE][INTERFACCIA] EVENTO dettagli
```

Il contesto e l'interfaccia possono mancare. La severità indica il livello del
messaggio; `MASTER`, `MACSEC`, `STATUS` e gli altri tag successivi indicano
invece quale parte del runtime lo ha emesso. Non sono facility o severity
syslog. L'evento descrive cosa è successo; i campi `nome=valore` precisano
peer, interfaccia, key ID, generazione, esito o motivo.

### Severity

| Tag | Cosa comunica |
|---|---|
| `DEBUG` | Dettaglio diagnostico; normalmente escluso dalla soglia predefinita `INFO`. |
| `INFO` | Avanzamento, successo o osservazione. Leggere comunque l'evento: anche un fatto non ancora confermato può essere registrato a livello INFO. |
| `WARN` / `WARNING` | Condizione anomala o recuperabile; verificare motivo e messaggi successivi. |
| `ERROR` | Operazione fallita, controllo non superato o attività bloccata. Verificare impatto e recupero, non solo la singola riga. |

La soglia `log_level` è configurabile; il runtime riconosce `DEBUG`, `INFO`,
`WARN`, `WARNING` ed `ERROR`.

### Tag di contesto

| Tag | Cosa cercare in quella famiglia |
|---|---|
| `MASTER` | Ciclo master, richiesta ENC al KME, scelta/skip/blocco della rotazione e coordinamento con il peer. |
| `SLAVE` | Richiesta d'installazione ricevuta dal peer, DEC, pianificazione e conferma dell'installazione lato slave. |
| `MACSEC` | Operazioni e verifiche Junos su keychain, CA e associazione all'interfaccia. |
| `MKA` | Stato della sessione MKA, corrispondenza della chiave/CKN e promozione della chiave pending. |
| `STATE` | Salvataggio e riconciliazione dello stato persistente. |
| `BOOTSTRAP` | Inizializzazione e installazione della chiave iniziale. |
| `CONFIG` | Validazione della configurazione locale e del link. |
| `STATUS` | Richiesta o aggiornamento dello stato esportato per il peer. |
| `LOCK` | Acquisizione, rilascio, contesa o gestione di un lock. |
| `RPC-KEY-ROTATION` | Rotazione dell'identità SSH/RPC del runtime; è distinta dalla rotazione CAK/MACsec. |

Il tag SAE identifica il contesto locale (nei log storici può apparire con
underscore, per esempio `sae_001`, mentre la configurazione può usare il
trattino). L'interfaccia identifica il link locale coinvolto.

## Significato delle principali famiglie di righe

Leggere la sequenza per fasi: un messaggio `START` non prova il completamento
dell'operazione e un messaggio di esito va correlato con quello del peer e con
lo stato MKA/MACsec.

| Evento o famiglia | Cosa dice |
|---|---|
| `SCRIPT START`, `MASTER START` | È iniziata un'invocazione o un ciclo master; non implica che sia avvenuta una rotazione. |
| `ENC OK` / `ENC FAIL` | Esito dell'ottenimento della chiave dal KME sul master. `key_id` collega l'evento ai passi successivi. |
| `INSTALL-KEY REQUEST`, `DEC OK` / `DEC FAIL` | Il peer ha chiesto l'installazione e lo slave ha decifrato (o non è riuscito a decifrare) la chiave. |
| `KEYCHAIN INSTALL START/OK/FAIL` | Avvio ed esito dell'installazione della chiave nella keychain Junos. `OK` attesta l'operazione locale, non la conferma MKA. |
| `INTERFACE BIND ...` | Esito dell'associazione della CA all'interfaccia. È distinto dall'installazione della chiave. |
| `MKA KEY CONFIRMED` | L'evidenza MKA corrisponde alla chiave attesa; controllare key ID/CKN e link. |
| `MKA KEY NOT CONFIRMED`, `PENDING KEY NOT YET CONFIRMED` | La chiave pending non è ancora confermata dall'osservazione MKA corrente. Può essere transitorio: cercare verifiche successive e confrontare entrambi i peer. |
| `PENDING KEY PROMOTED` | Dopo la conferma MKA, lo stato runtime ha promosso la chiave pending ad attiva. |
| `ROTATION SKIP ... reason=...` | Il ciclo ha deciso di non ruotare. Il valore `reason` spiega la condizione, ad esempio chiave pending non ancora dovuta o rekey disabilitato. |
| `ROTATION BLOCKED`, `... FAIL`, `... ERROR` | L'operazione non può proseguire o è fallita. Usare `reason`, fase, ACK e righe successive per individuare il punto e verificare un eventuale recupero. |
| `STATE SAVED` / `STATE SAVE ERROR` | Esito della persistenza dello stato link. Il percorso indicato aiuta a individuare il relativo JSON. |
| `SSH EXEC`, `SSH RC`, ACK peer | Evidenza del trasporto/comando e della risposta applicativa. Un return code SSH pari a zero, da solo, non prova che l'azione remota sia stata applicata: verificare l'ACK applicativo e lo stato risultante. |
| `ACTION LOCK ...`, `MASTER LOCK ...` | Contesa o ciclo di vita del lock. Un lock occupato può far rinviare l'azione; non rimuoverlo senza seguire la procedura di recovery. |
| `RPC-KEY-ROTATION` / `RPC KEY ...` | Passi della rotazione della chiave pubblica SSH/RPC. Non confonderli con ENC/DEC o con una rotazione MACsec. |
| `PEER STATUS ...` | Aggiornamento/esportazione dello snapshot di stato; lo snapshot è diagnostico e può essere meno recente dello stato live. |

I nomi sono famiglie rappresentative, non un elenco di stringhe esaustivo.
Usare i dettagli della riga e le righe vicine per interpretare la fase.

## File di log e stato da consultare

I percorsi sono configurabili dal runtime; i valori effettivi vanno verificati
nei sidecar distribuiti. Nel layout usuale:

| File | A cosa serve |
|---|---|
| `qkd_debug.log` | Log combinato del runtime locale: sequenza di eventi di tutti i link gestiti dal dispositivo. |
| `qkd_debug_<SAE>_<interfaccia>.log` | Vista per link delle righe che includono un'interfaccia; `/` nel nome interfaccia diventa `_`. |
| `qkd_debug.log.1`, `.2`, ... | Generazioni ruotate del log combinato; `.1` è la più recente. La rotazione è per dimensione e il numero di copie dipende dalla configurazione. |
| `qkd_db_<peer>_<interfaccia>.json` | Stato persistente del ring per uno specifico peer/link: generazione, chiave attiva/pending, slot ed eventuale installazione in corso. |
| `qkd_peer_status_<SAE>_<interfaccia>.json` | Snapshot esportato dello stato peer/link; controllarne l'istante di aggiornamento perché può essere obsoleto. |
| `qkd_rpc_key_rotation.json` | Stato della transazione di rotazione dell'identità SSH/RPC, separata dalle chiavi MACsec. |
| `qkd_batch_pipeline_timing.jsonl`, `qkd_rolling_pipeline_timing.jsonl` | Misure di durata: una registrazione JSON per riga. Servono per analisi dei tempi, non sostituiscono la sequenza del log. |

Prima di cercare un file, ricavare `LOG_FILE`, `LOG_DIR`, `STATE_DIR` e
`PEER_STATUS_DIR` dalla configurazione effettiva. L'ispezione dei JSON deve
restare in sola lettura; per interpretazione e recovery del database link,
seguire [State Inspection](../../onbox/state_inspection.md). Per snapshot e
report off-box, consultare
[Collection and Analytics](../../tools/collection_and_analytics.md).

## Ricerca pratica

Su uno snapshot locale, `grep -F` cerca i tag tra parentesi quadre in modo
letterale:

```sh
grep -F '[ERROR]' qkd_debug.log*
grep -F '[MACSEC]' qkd_debug.log*
grep -F '[MKA]' qkd_debug.log*
grep -F '[STATUS]' qkd_debug.log*
grep -F 'ROTATION SKIP' qkd_debug.log*
grep -F '[ERROR]' qkd_debug.log* | grep -F '[MASTER]'
grep -n -B 2 -A 8 -F 'MKA KEY NOT CONFIRMED' qkd_debug.log
```

Gli output MKA possono continuare su più righe senza ripetere timestamp e
tag: perciò un filtro per severity può mostrare solo l'intestazione
dell'evento. Usare `-A`/`-B` per mantenere il contesto. Le marche temporali
sono locali al router: prima di correlare i due lati verificare la
sincronizzazione degli orologi e usare anche interfaccia, peer, key ID e
generazione.

I log e gli stati possono contenere topologia, identità, key ID, nomi CA,
percorsi SSH e dettagli operativi. Redigere i dati prima di condividerli;
non inserire password, chiavi private, token o materiale chiave nei log o in
Git.
