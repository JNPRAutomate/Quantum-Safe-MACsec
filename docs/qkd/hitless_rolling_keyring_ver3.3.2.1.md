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

- bootstrap: l'orchestrator configura soltanto lo slot 0
- completion: `qkd_onbox.py` aggiunge gli altri `N-1` slot
- steady state: active e pending sono protetti come coppia operativa
- ogni batch successivo sostituisce gli altri `N-2` slot

La coppia `[active, pending]` non è una transazione distribuita atomica: i due
router eseguono commit indipendenti. La sicurezza deriva da snapshot
bilaterale, start-time futuri, ACK sincrono via RPC e recovery persistente.

## 2. Timer distinti

La policy corrente usa:

```yaml
execution_interval_seconds: 60
key_activation_interval_seconds: 300
peer_batch_ack_timeout_seconds: 150
peer_enqueue_min_margin_seconds: 60
adaptive_grace_history_size: 32
adaptive_grace_floor_seconds: 150
adaptive_grace_safety_margin_seconds: 30
adaptive_grace_rounding_seconds: 60
rpc_key_rotation_interval_seconds: 600
```

I timer hanno funzioni indipendenti:

| Timer | Funzione |
|---|---|
| Execution interval | Frequenza con cui Junos esegue `qkd_onbox.py` |
| Activation interval | Distanza tra gli start-time di due chiavi consecutive |
| RPC duration limit | Limite massimo della chiamata RPC verso il peer |
| Enqueue/install margin | Ultimo controllo prima dell'installazione del batch |
| Adaptive grace | Lead time calcolato dai tempi reali delle transazioni |
| RPC identity rotation | Rotazione ED25519 indipendente di `qkd_rpc_id_ed25519` |

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

Per ogni transazione conclusa con esito positivo vengono misurati:

```text
t0 = richiesta commit locale
t1 = commit locale terminato
t2 = invio batch al peer
t3 = risposta positiva del peer

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

Timeout, errori KME, errori SSH e risposte negative non entrano nello storico e
non possono ridurre il grace. Se il grace osservato supera la finestra
protetta, il runtime blocca il replacement invece di schedulare chiavi senza
margine.

## 4. Bootstrap e completion

L'orchestrator configura:

| Slot | Origine | Start-time |
|---|---|---|
| 0 | Bootstrap deterministico | Passato |
| 1..N-1 | Vuoto | - |

`qkd_onbox.py` adotta lo slot 0 senza eseguire ENC, verifica MKA e completa gli
slot mancanti.

## 5. Replacement N-2 con quattro slot

Quando lo snapshot bilaterale mostra:

```text
slot 2 = active
slot 3 = pending
slot 0 = consumed
slot 1 = consumed
```

la coppia `{2,3}` è protetta e il singolo commit sostituisce `{0,1}`.

## 6. Guardie bilaterali

Prima di generare nuove chiavi il runtime richiede:

1. MACsec `inuse`
2. active key e active slot uguali sui due peer
3. pending slot uguale sui due peer
4. active e pending adiacenti nel ring
5. stesso insieme di slot configurati
6. stessi key-id e start-time per ogni slot
7. tutti gli N-2 target già consumati
8. nessuna transazione inflight precedente
9. grace compatibile con la finestra protetta

## 7. Errori e recovery inflight

Prima del commit il master salva una transazione persistente contenente payload,
record e `t0`. Dopo il commit salva `t1`; prima dell'invio salva `t2`.

Se trasporto o risposta peer falliscono:

1. la transazione non viene cancellata
2. non vengono generate altre chiavi
3. viene ritentato lo stesso payload
4. il recovery precede il controllo MACsec `inuse`
5. lo stato viene finalizzato soltanto dopo esito positivo

## 8. Pending legacy

Il vecchio recovery del full-batch resta soltanto come contesto storico. Il
percorso attivo `run_master_rolling_link()` usa invece:

- snapshot active/pending locale e peer
- metadata bilaterali
- inflight persistente
- risposta peer sincrona
- grace adattivo

## 9. Rotazione RPC indipendente

La chiave ED25519 di runtime (`qkd_rpc_id_ed25519`) ruota ogni 600 secondi ed è
indipendente dalle CAK MACsec. `qkd_onbox.py` completa prima il lavoro MACsec e
soltanto dopo tenta la rotazione dell'identità RPC, evitando cambi credenziali
durante una transazione MACsec.

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
ROTATION SELF_HEAL reason=NO_FUTURE_SLOT_SCHEDULED rearm_slots=[...]
INFLIGHT RETRY
INFLIGHT FINALIZED
ROTATION BLOCKED reason=ACTIVE_NOT_BILATERALLY_CONFIRMED
ROTATION BLOCKED reason=NEXT_KEY_NOT_BILATERALLY_CONFIRMED
ROTATION BLOCKED reason=ACTIVE_PENDING_PAIR_NOT_ADJACENT
ROTATION BLOCKED reason=ADAPTIVE_GRACE_EXCEEDS_PROTECTED_HORIZON
ROTATION SKIP reason=N_MINUS_TWO_TARGETS_NOT_CONSUMED
ROTATION SKIP reason=ROTATION_TOO_SOON ...
RPC-KEY-STATE: interval_seconds=...
RPC-KEY ROTATION START ...
OK PREPARE-RPC-PUBKEY source_device=...
OK FINALIZE-RPC-PUBKEY source_device=...
```

Comandi Junos:

```text
show configuration security authentication-key-chains key-chain <name> | display set
show security mka sessions detail
show security macsec connections
```

## 11. Troubleshooting safe: riallineamento di `key 0`

Durante recovery o redeploy, controllare sempre `key 0` su entrambi i lati del
link. Per il bootstrap pulito devono essere identici:

- `key-name`
- `secret`
- `start-time`

Se `key 0` differisce tra i due lati, il bootstrap non può convergere.

## 12. Confronto con modelli esterni

Le idee riutilizzabili sono la verifica preventiva della sincronizzazione
temporale e il controllo dei configuration lock. Il modello N-2 e la rotazione
RPC separata restano specifici di questo runtime QKD distribuito.
