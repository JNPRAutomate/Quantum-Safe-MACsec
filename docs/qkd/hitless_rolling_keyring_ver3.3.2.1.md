# MACsec Hitless Rolling Keyring a Quattro Slot

Versione: `ver3.3.2.1`

## 1. Obiettivo

Il keychain Junos usa quattro slot fisici:

```text
0, 1, 2, 3
```

Il runtime deve mantenere sempre:

- una chiave attiva;
- una chiave successiva con `start-time` futuro;
- ulteriore capacità futura;
- lo stesso contenuto e gli stessi start-time sui due peer.

Dopo il bootstrap, `qkd_orchestrator.py` non partecipa più al ciclo delle
chiavi. Tutte le chiamate ENC/DEC al KME, l'installazione e il rolling sono
eseguiti esclusivamente da `qkd_onbox.py`.

## 2. Responsabilità

### qkd_orchestrator

Durante il provisioning:

1. crea il keychain;
2. configura soltanto lo slot 0;
3. usa un CKN e un CAK bootstrap deterministici;
4. imposta lo start-time nel passato;
5. abilita MACsec e quindi la raggiungibilità verso il KME;
6. installa `qkd_onbox.py`.

Non esegue chiamate ENC al KME durante il bootstrap.

### qkd_onbox

Quando viene eseguito sul router:

1. adotta lo slot 0 già configurato;
2. verifica che MKA lo stia usando;
3. verifica che entrambi i peer abbiano lo stesso active;
4. esegue ENC sul KME per completare gli slot 1, 2 e 3;
5. gestisce successivamente il ring uno slot alla volta.

## 3. Stati del ring

| Stato | Significato |
|---|---|
| `uninitialized` | Lo stato runtime non ha ancora adottato il keychain |
| `seeded` | Lo slot 0 bootstrap è stato adottato e confermato da MKA |
| `ready` | Tutti gli slot 0, 1, 2 e 3 sono presenti |

La transizione normale è:

```text
uninitialized -> seeded -> ready -> rolling continuo
```

## 4. Regole di sicurezza

Uno slot può essere riscritto soltanto quando:

1. non è la chiave attiva locale;
2. non è la chiave attiva sul peer;
3. entrambi i peer confermano la stessa chiave attiva;
4. non è la prossima chiave determinata dallo start-time;
5. entrambi i peer confermano lo stesso next slot;
6. è lo slot precedentemente attivo e ora ritirato su entrambi;
7. il nuovo start-time lascia il margine necessario a commit, trasporto e ACK.

In caso di stato ambiguo il comportamento è fail-closed:

```text
nessuna nuova ENC
nessun nuovo slot
nessun avanzamento software
rotazione bloccata
```

`pending_auto_evict_enabled` è disabilitato. Una pending non confermata non
viene eliminata automaticamente dallo stato software.

## 5. Esempio 1: bootstrap con solo slot 0

Il provisioning produce:

| Slot | Origine | Start-time | Stato |
|---|---|---|---|
| 0 | Orchestrator | passato | active |
| 1 | vuoto | - | - |
| 2 | vuoto | - | - |
| 3 | vuoto | - | - |

Esempio:

```text
ora              = 10:00
slot 0 start-time = 2026-01-01 00:01
```

Junos può usare immediatamente lo slot 0. MACsec diventa operativo e il router
ottiene raggiungibilità verso il KME.

`qkd_onbox.py` non esegue ENC per ricreare lo slot 0. Ricostruisce invece
l'identità bootstrap deterministica, verifica il CKN configurato e conferma
tramite MKA che lo slot 0 sia realmente in uso.

## 6. Esempio 2: completamento iniziale degli slot 1, 2 e 3

Con la policy corrente:

```text
interval_seconds                  = 120
batch_activation_margin_seconds  = 480
peer_batch_ack_timeout_seconds    = 210
```

Alle 10:00 `qkd_onbox.py` genera tre chiavi KME:

| Slot | Start-time | Ruolo iniziale |
|---|---|---|
| 0 | passato | active bootstrap |
| 1 | 10:08 | next |
| 2 | 10:10 | future |
| 3 | 10:12 | future |

Flusso:

1. il master esegue tre ENC;
2. salva una transazione `inflight_install`;
3. esegue il commit locale degli slot 1, 2 e 3;
4. invia al peer gli stessi key-id, slot e start-time;
5. il peer esegue DEC e commit;
6. il peer scrive l'ACK;
7. solo dopo `ACK=ok` il master finalizza lo stato `ready`.

Durante tutto il completamento lo slot 0 resta configurato e attivo.

## 7. Esempio 3: primo rolling, da slot 0 a slot 1

Alle 10:08 Junos seleziona lo slot 1 in base allo start-time.

Prima del riuso:

```text
slot 0 = precedente active, ora ritirato
slot 1 = active
slot 2 = next
slot 3 = future
```

Il runtime non riscrive immediatamente lo slot 0. Prima richiede:

```text
local active = slot 1
peer active  = slot 1
local next   = slot 2
peer next    = slot 2
local retired slot = 0
peer retired slot  = 0
```

Solo dopo queste conferme lo slot 0 può ricevere una nuova chiave:

| Slot | Start-time | Stato |
|---|---|---|
| 1 | 10:08 | active |
| 2 | 10:10 | next |
| 3 | 10:12 | future |
| 0 | 10:14 | nuova future |

Il ring temporale è quindi:

```text
1 -> 2 -> 3 -> 0
```

L'ordine temporale è preservato dagli start-time. Il numero di slot è un
identificatore fisico riutilizzato circolarmente.

## 8. Esempio 4: rotazioni successive

### Quando lo slot 2 diventa active

```text
slot 1 = retired
slot 2 = active
slot 3 = next
slot 0 = future
```

Il runtime sostituisce solo lo slot 1:

```text
slot 1 start-time = 10:16
```

### Quando lo slot 3 diventa active

```text
slot 2 = retired
slot 3 = active
slot 0 = next
slot 1 = future
```

Il runtime sostituisce solo lo slot 2:

```text
slot 2 start-time = 10:18
```

### Quando il nuovo slot 0 diventa active

```text
slot 3 = retired
slot 0 = active
slot 1 = next
slot 2 = future
```

Il runtime sostituisce solo lo slot 3:

```text
slot 3 start-time = 10:20
```

La sequenza continua:

```text
active: 0 -> 1 -> 2 -> 3 -> 0 -> 1 -> 2 -> 3
time:   crescente senza interruzioni
```

## 9. Esempio 5: errore ENC verso il KME

Se ENC fallisce prima del commit:

```text
nessun nuovo key-id disponibile
nessuna modifica Junos
nessun invio al peer
nessun avanzamento dello stato
```

La chiave active e le future già installate continuano a essere usate.

## 10. Esempio 6: errore SSH o ACK dopo il commit locale

I commit su due router non sono atomici. Per questo il master salva prima una
transazione persistente:

```json
{
  "operation": "ROLLING_REPLACEMENT",
  "records": [],
  "payload_b64": "...",
  "ack_id": "...",
  "created_at": 1785470000
}
```

Se SCP o ACK falliscono:

1. `inflight_install` non viene cancellata;
2. non viene generata una nuova chiave;
3. non viene scelto un altro slot;
4. al ciclo successivo viene ritentato lo stesso payload con lo stesso ACK ID;
5. il recovery viene tentato anche se MACsec non risulta più `inuse`;
6. lo stato viene finalizzato soltanto dopo ACK positivo.

Nel rolling normale lo slot modificato è già ritirato. Active e next non
vengono toccati dalla transazione.

La garanzia hitless richiede comunque che la connettività di controllo venga
ripristinata prima dello start-time della nuova chiave. Nessun algoritmo con
due commit indipendenti può garantire atomicità durante una partizione SSH
prolungata oltre il margine di attivazione.

## 11. Esempio 7: pending non confermata

Situazione:

```text
slot 1 dovrebbe essere active
MKA locale o peer non lo conferma
```

Il runtime:

- non elimina la pending;
- non considera automaticamente completato il rollover;
- non riscrive lo slot 0;
- non avanza verso una nuova generazione;
- attende riconciliazione MKA e peer.

Questo impedisce che lo stato software diverga dalla configurazione Junos.

## 12. Rotazione SSH indipendente

La chiave ED25519 di `etsi_peer_view` è indipendente dalle CAK MACsec.

Per evitare che la credenziale cambi durante SCP, status o ACK del keyring,
`qkd_onbox.py` esegue:

```text
1. ciclo MACsec/keyring
2. eventuale rotazione SSH etsi_peer_view
```

La rotazione SSH resta abilitata secondo:

```yaml
peer_key_rotation_interval_seconds: 300
```

Non determina slot, start-time, active o next del keychain MACsec.

## 13. Condizioni che bloccano il rolling

| Condizione | Risultato |
|---|---|
| Active locale diverso dal peer | Blocco |
| Active slot non determinabile | Blocco |
| Next locale diverso dal peer | Blocco |
| Metadata slot differenti | Blocco |
| Retired slot differente | Blocco |
| Retired slot uguale ad active | Blocco |
| Retired slot uguale a next | Blocco |
| Stato parziale diverso dal solo seed `{0}` | Blocco |
| Transazione inflight non conclusa | Retry della stessa transazione |

## 14. Log operativi principali

Bootstrap e completion:

```text
ORCHESTRATOR SEED ADOPTED
RING_COMPLETION START
RING_COMPLETION DONE
```

Rolling:

```text
ROLLING_REPLACEMENT START
ROLLING_REPLACEMENT DONE
```

Recovery:

```text
INFLIGHT RETRY
INFLIGHT FINALIZED
ROTATION BLOCKED reason=INFLIGHT_INSTALL_NOT_CONFIRMED
```

Guard:

```text
ROTATION BLOCKED reason=ACTIVE_NOT_BILATERALLY_CONFIRMED
ROTATION BLOCKED reason=NEXT_KEY_NOT_BILATERALLY_CONFIRMED
ROTATION BLOCKED reason=RETIRED_SLOT_MISMATCH
ROTATION BLOCKED reason=RETIRED_SLOT_STILL_PROTECTED
```

## 15. Verifica sui router

Su entrambi i peer:

```text
show configuration security authentication-key-chains key-chain <name> | display set
show security mka sessions detail
show security macsec connections
```

Verificare che:

1. gli stessi slot abbiano gli stessi CKN e start-time;
2. MKA sia secured su entrambi;
3. active e next coincidano;
4. ogni ciclo rolling modifichi un solo slot;
5. lo slot modificato sia quello precedentemente active;
6. non compaiano eventi `UNKNOWN_CAK`;
7. MACsec resti `inuse`;
8. ping, LACP e adiacenze di routing non subiscano interruzioni.

## 16. Limite della validazione

I test automatici verificano il planner, i guard e l'ordine delle operazioni.
La proprietà hitless end-to-end deve essere confermata sui due ACX con traffico
continuo durante almeno un intero ciclo:

```text
0 -> 1 -> 2 -> 3 -> 0
```

Solo il test hardware dimostra il comportamento specifico della release Junos
EVO in uso.

