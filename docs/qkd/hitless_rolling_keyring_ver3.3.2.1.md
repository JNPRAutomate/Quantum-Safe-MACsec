# MACsec Hitless Rolling Keyring N-2

Versione: `ver3.3.2.1`

## 0. Versioning script principali

Per la release `ver3.3.2.1` i tre entrypoint principali espongono versioning
esplicito:

- `qkd_orchestrator.py --version`
- `kme_orchestrator.py --version`
- `artifacts/qkd_onbox.py --version`

Nel runtime `qkd_onbox.py`, lo stato esportato include anche
`script_version=ver3.3.2.1` per correlare snapshot/peer status con la versione
on-box effettiva.

## 1. Modello

Il keyring contiene un numero pari `N` di slot, con `4 <= N <= 64`. Quattro è
il minimo perché una coppia active/pending lascia almeno due slot sostituibili.

- bootstrap: l'orchestrator configura soltanto lo slot 0;
- completion: `qkd_onbox.py` aggiunge gli altri `N-1` slot;
- steady state: active e pending sono protetti come coppia operativa;
- ogni batch successivo sostituisce gli altri `N-2` slot.

La coppia `[active, pending]` non è una transazione distribuita atomica: i due
router eseguono commit indipendenti. La sicurezza deriva da snapshot bilaterale,
start-time futuri, ACK e recovery persistente.

## 2. Timer distinti

La policy corrente usa:

```yaml
execution_interval_seconds: 60
key_activation_interval_seconds: 300
peer_batch_ack_timeout_seconds: 150
peer_batch_ack_poll_interval_seconds: 5
peer_enqueue_min_margin_seconds: 60
adaptive_grace_history_size: 32
adaptive_grace_floor_seconds: 150
adaptive_grace_safety_margin_seconds: 30
adaptive_grace_rounding_seconds: 60
peer_key_rotation_interval_seconds: 600
```

I timer hanno funzioni indipendenti:

| Timer | Funzione |
|---|---|
| Execution interval | Frequenza con cui Junos esegue `qkd_onbox.py` |
| Activation interval | Distanza tra gli start-time di due chiavi consecutive |
| ACK timeout | Limite massimo di attesa dell'ACK peer |
| ACK poll | Frequenza di lettura dell'ACK |
| Enqueue margin | Ultimo controllo prima di accodare il payload |
| Adaptive grace | Lead time calcolato dai tempi reali delle transazioni |
| SSH rotation | Rotazione ED25519 indipendente di `etsi_peer_view` |

La copertura nominale del keyring è:

```text
coverage = N * key_activation_interval
```

Con quattro slot:

```text
coverage = 4 * 300 = 1200 secondi
```

Un batch steady-state contiene invece:

```text
replacement_count = N - 2
```

Con quattro slot vengono quindi sostituite due chiavi per transazione.

## 3. Grace adattivo

Per ogni transazione conclusa con ACK positivo vengono misurati:

```text
t0 = richiesta commit locale
t1 = commit locale terminato
t2 = invio payload al peer
t3 = ACK positivo ricevuto

delta_commit = t1 - t0
delta_ack    = t3 - t1
delta_total  = t3 - t0
```

Lo stato conserva le ultime 32 transazioni riuscite:

```text
grace = ceil(
    max(configured_floor, max(last_32_successful_delta_total))
    + safety_margin,
    rounding
)
```

Con storico vuoto e policy corrente:

```text
grace = ceil(max(150, 0) + 30, 60) = 180 secondi
```

Timeout, errori KME, errori SSH e ACK negativi non entrano nello storico e non
possono ridurre il grace. Se il grace osservato supera la finestra protetta, il
runtime blocca il replacement invece di schedulare chiavi senza margine.

Per il modello N-2 la verifica conservativa è:

```text
maximum_safe_grace =
    2 * key_activation_interval - execution_interval
```

Con i valori correnti:

```text
maximum_safe_grace = 2 * 300 - 60 = 540 secondi
```

Il grace iniziale di 180 secondi è ammesso e sostituisce il precedente margine
fisso di 480 secondi. Oltre 540 secondi il batch viene bloccato e richiede un
intervallo di attivazione più ampio o un'esecuzione più frequente.

## 4. Bootstrap e completion

L'orchestrator configura:

| Slot | Origine | Start-time |
|---|---|---|
| 0 | Bootstrap deterministico | Passato |
| 1..N-1 | Vuoto | - |

`qkd_onbox.py` adotta lo slot 0 senza eseguire ENC, verifica MKA e completa gli
slot mancanti. Con N=4 e invocazione alle 10:00:

| Slot | Start-time | Ruolo |
|---|---|---|
| 0 | passato | active bootstrap |
| 1 | 10:03 | pending |
| 2 | 10:08 | future |
| 3 | 10:13 | future |

Il primo start-time deriva dal grace iniziale di 180 secondi; i successivi sono
separati dall'activation interval di 300 secondi.

## 5. Replacement N-2 con quattro slot

Quando lo snapshot bilaterale mostra:

```text
slot 2 = active
slot 3 = pending
slot 0 = consumed
slot 1 = consumed
```

la coppia `{2,3}` è protetta e il singolo commit sostituisce `{0,1}`:

```text
slot 0 = nuova future key
slot 1 = nuova future key
```

L'ordine temporale dei target parte dallo slot successivo alla pending. Alcuni
esempi:

| Active | Pending | Target ordinati |
|---:|---:|---|
| 0 | 1 | 2, 3 |
| 1 | 2 | 3, 0 |
| 2 | 3 | 0, 1 |
| 3 | 0 | 1, 2 |

Per N=60, con active 57 e pending 58:

```text
protected = {57, 58}
targets   = 59, 0, 1, ..., 56
count     = 58
```

## 6. Guardie bilaterali

Prima di generare nuove chiavi il runtime richiede:

1. MACsec `inuse`;
2. active key e active slot uguali sui due peer;
3. pending slot uguale sui due peer;
4. active e pending adiacenti nel ring;
5. stesso insieme di slot configurati;
6. stessi key-id e start-time per ogni slot;
7. tutti gli N-2 target hanno start-time precedente a quello dell'active;
8. nessuna transazione inflight precedente;
9. grace compatibile con la finestra protetta.

In caso di ambiguità:

```text
nessuna nuova ENC
nessun nuovo commit
nessun avanzamento software
```

## 7. Errori e recovery inflight

Prima del commit il master salva una transazione persistente contenente payload,
ACK ID, record e `t0`. Dopo il commit salva `t1`; prima dell'invio salva `t2`.

Se trasporto o ACK falliscono:

1. la transazione non viene cancellata;
2. non vengono generate altre chiavi;
3. viene ritentato lo stesso payload con lo stesso ACK ID;
4. il recovery precede il controllo MACsec `inuse`;
5. lo stato viene finalizzato soltanto dopo ACK positivo.

Solo allora viene registrato `t3` e il campione entra nello storico adattivo.

## 8. Pending legacy

`pending_confirm_grace_seconds` e `pending_stuck_recovery_seconds` appartengono
al precedente full-batch, conservato nel file soltanto come codice storico
irraggiungibile. Il percorso attivo `run_master_rolling_link()` non usa tali
timer. La protezione corrente è fornita da:

- snapshot active/pending locale e peer;
- metadata bilaterali;
- inflight persistente;
- ACK;
- grace adattivo.

`pending_auto_evict_enabled` resta disabilitato: il runtime non elimina stato
pending per forzare artificialmente un avanzamento.

## 8bis. Self-healing del ring esaurito (RING_REARM)

Prima di questa modifica, se **nessuno** slot configurato aveva più un
`start-time` futuro (ring esaurito), `select_ring_update_slots()` restituiva
`ACTIVE_PENDING_PAIR_INCOMPLETE` e `run_master_rolling_link()` si fermava con
`ROTATION BLOCKED`. Non esisteva alcun altro percorso capace di scrivere un
nuovo `start-time` futuro in quello stato: il blocco era quindi permanente e
assorbente. Bastava un singolo fallimento transitorio (KME irraggiungibile,
SSH verso il peer, `commit lock` occupato, timeout ACK, un tick di cron
saltato) perché tutti gli slot scadessero prima che una sostituzione andasse
a buon fine, lasciando il link bloccato per sempre e richiedendo un intervento
manuale.

Dalla versione corrente, quando l'`active_slot` è valido (e già confermato
bilateralmente dai controlli precedenti) ma nessuno slot ha un `start-time`
futuro, `select_ring_update_slots()` restituisce invece un'operazione
`RING_REARM` con target lo slot immediatamente successivo all'active
(`(active_slot + 1) % N`). `run_master_rolling_link()` la gestisce riusando
**la stessa pipeline bilaterale** di una rotazione normale: ENC dal KME,
commit locale, invio SSH `install-key-batch` al peer, attesa ACK,
finalizzazione bilaterale. Non viene introdotto nessun nuovo canale di
comunicazione né alcuna possibilità di disallineamento tra i due router: è
esattamente la stessa transazione atomica already-existing, applicata a un
solo slot invece che a `N-2`.

Il precondition check `ACTIVE_NOT_BILATERALLY_CONFIRMED` (active key e slot
identici su entrambi i lati) resta invariato e viene eseguito **prima** di
questo branch: `RING_REARM` scatta solo se l'active slot è già confermato
bilateralmente, mai in presenza di ambiguità sullo stato attivo.

`ACTIVE_PENDING_PAIR_INCOMPLETE` come `block_reason` non viene più prodotto:
i restanti motivi di blocco (`INVALID_RING_SHAPE`, `NOT_CLEAN_SEED`,
`ACTIVE_SLOT_INVALID`, `ACTIVE_PENDING_PAIR_NOT_ADJACENT`,
`NO_REPLACEABLE_SLOTS`) restano condizioni reali che richiedono
investigazione manuale e continuano a produrre `ROTATION BLOCKED`.

## 9. Rotazione SSH indipendente

La chiave ED25519 di `etsi_peer_view` ruota ogni 600 secondi ed è indipendente
dalle CAK MACsec. `qkd_onbox.py` completa prima tutto il lavoro MACsec e soltanto
dopo tenta la rotazione SSH, evitando cambi credenziali durante una transazione.

## 10. Log e verifica

Log principali:

```text
ORCHESTRATOR SEED ADOPTED
RING_COMPLETION START
RING_COMPLETION DONE
ROLLING_REPLACEMENT START
ROLLING_REPLACEMENT DONE
RING_REARM START
RING_REARM DONE
ROTATION SELF_HEAL reason=NO_FUTURE_SLOT_SCHEDULED
INFLIGHT RETRY
INFLIGHT FINALIZED
ROTATION BLOCKED reason=ACTIVE_NOT_BILATERALLY_CONFIRMED
ROTATION BLOCKED reason=NEXT_KEY_NOT_BILATERALLY_CONFIRMED
ROTATION BLOCKED reason=ACTIVE_PENDING_PAIR_NOT_ADJACENT
ROTATION BLOCKED reason=ADAPTIVE_GRACE_EXCEEDS_PROTECTED_HORIZON
ROTATION SKIP reason=N_MINUS_TWO_TARGETS_NOT_CONSUMED
```

Comandi Junos:

```text
show configuration security authentication-key-chains key-chain <name> | display set
show security mka sessions detail
show security macsec connections
```

Verificare che active/pending coincidano, che ogni batch modifichi esattamente
N-2 slot, che gli slot protetti non cambino e che MACsec resti `inuse`.

## 11. Confronto con adammmmm/hitless-key-rollover

Il progetto esterno adotta lo stesso principio generale: usa gli start-time
Junos per preparare in anticipo nuove CKN/CAK e verifica che tutti i router
condividano la stessa chiave attiva prima dei commit.

L'algoritmo è però diverso:

| Progetto esterno | Questo runtime |
|---|---|
| Controller Python esterno con PyEZ | Runtime autonomo on-box |
| 32 slot, protegge il solo active e sostituisce gli altri 31 | N slot, protegge active e pending e sostituisce N-2 |
| Parte soltanto quando Junos non riporta una next key | Richiede sempre una pending bilaterale |
| Genera CAK/CKN casuali centralmente | Ottiene chiavi dal KME tramite ENC/DEC |
| Commit sequenziali e rollback compensativo | Commit locale, coda peer, ACK e inflight persistente |
| `ROLLINTERVAL` fisso in ore | Activation interval distinto dal tick di esecuzione |
| Nessuna misura adattiva | Grace derivato dalle ultime 32 transazioni riuscite |

Le idee riutilizzabili sono la verifica preventiva della sincronizzazione
temporale e il controllo dei configuration lock. Non viene importato codice dal
progetto esterno; il modello N-2 resta specifico per il keyring QKD distribuito.
