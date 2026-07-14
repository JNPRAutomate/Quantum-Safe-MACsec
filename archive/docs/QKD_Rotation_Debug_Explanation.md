# QKD MACsec Key Rotation – Human Readable Walkthrough

## Overview

This debug log shows a successful QKD-driven MACsec key rotation process running on ACX routers.

The design uses:

- A stable MACsec Connectivity Association (CA)
- A stable MACsec authentication key-chain
- Scheduled future key activation
- MKA confirmation before a key becomes active
- Master/Slave synchronization through SSH

The system does **not** immediately switch keys after installation.
A new key is installed first, scheduled for a future start time, and only later becomes active when MKA confirms its use.

---

## Rotation Timing Observed

### CA1 Link (et-2/0/4)

Observed sequence:

- Pending key confirmed at **17:59:39**
- New rotation started at **17:59:40**
- New key scheduled for activation at **18:06:00**

This means:

- Rotation planning begins immediately after the previous key becomes active.
- The newly generated key is scheduled about **6 minutes in the future**.
- During this time the currently active key remains in service.

Observed duration:

- Rotation creation: a few seconds
- Scheduled activation delay: approximately **6 minutes**

---

### CA9 Link (et-2/0/2)

Observed sequence:

- Key confirmed at **18:00:34**
- Next key scheduled at **18:00:35**
- Activation time programmed for **18:05:00**

Observed duration:

- Approximately **5 minutes** between installation and activation.

---

## What Happens During One Rotation Cycle

### Step 1 – Existing Key Becomes Active

Example:

```
17:59:39 MKA KEY CONFIRMED
17:59:39 PENDING KEY PROMOTED
```

Meaning:

- MKA confirms that both routers are now using the pending key.
- The pending key becomes the active key.
- State database is updated.

---

### Step 2 – Master Starts New Rotation

Example:

```
17:59:40 KEYCHAIN ROTATION START
```

Meaning:

The master router decides a new key must be prepared.

The master:

- Generates a new QKD key from the KME.
- Calculates a future activation time.
- Increments the generation number.

---

### Step 3 – Master Retrieves New QKD Key

Example:

```
ENC OK key_id=9a1defcd-...
```

Meaning:

The master requests a fresh encryption key from the KME.

---

### Step 4 – Master Sends Install Request to Peer

Example:

```
SSH EXEC admin@100.123.170.201 action=install-key
```

Meaning:

The master contacts the peer router over SSH and instructs it to install the same future key.

---

### Step 5 – Peer Installs Scheduled Key

Example:

```
INSTALL-KEY REQUEST
INSTALL-KEY SCHEDULE
KEYCHAIN INSTALL OK
```

Meaning:

The slave router:

- Retrieves the same QKD key via DEC.
- Installs it into the MACsec authentication key-chain.
- Programs a future start-time.
- Does not activate it immediately.

---

## When Does the Commit Happen?

The commit happens during:

```
KEYCHAIN INSTALL START
KEYCHAIN INSTALL OK
```

Inside `install_keychain_key()` the script executes:

```
configure
set security authentication-key-chains ...
set security macsec ...
commit
exit
```

Therefore every key installation performs a Junos commit.

This occurs on:

1. The slave router first.
2. The master router immediately afterwards.

The log confirms successful commits because:

```
KEYCHAIN INSTALL OK
```

appears on both sides.

---

## Where Does the Commit Happen?

### Slave side

Triggered by:

```
action install-key
```

Executed through:

```
run_slave_install_key()
```

Which calls:

```
install_keychain_key()
```

That function performs the Junos configure/commit sequence.

---

### Master side

After the peer finishes:

```
install_keychain_key()
```

is executed locally.

The master also performs its own commit.

Result:

Both routers contain the same future key before activation time arrives.

---

## Why Rotation Is Hitless

The currently active key remains in service while the next key is being prepared.

The log repeatedly shows:

```
ROTATION SKIP
PENDING_KEY_SCHEDULED_NOT_DUE
```

Meaning:

- The new key exists.
- The scheduled start time has not arrived.
- No traffic changes occur yet.

This prevents service interruption.

---

## When Does the New Key Become Active?

Activation occurs when:

1. The configured start-time is reached.
2. MKA begins using the new CAK/CKN.
3. MKA reports the expected CKN.
4. The script logs:

```
MKA KEY CONFIRMED
PENDING KEY PROMOTED
```

Only at that moment does the state change from:

```
pending_key_id
```

to:

```
active_key_id
```

---

## Complete Example Timeline

### CA1 link

| Time | Event |
|--------|--------|
| 17:59:39 | Existing pending key confirmed |
| 17:59:39 | Pending key promoted to active |
| 17:59:40 | New rotation begins |
| 17:59:40 | New QKD key obtained |
| 17:59:40 | Peer install requested |
| 17:59:43 | Local install completed |
| 17:59:47 | New key stored as pending |
| 18:06:00 | Scheduled activation time |
| After 18:06 | MKA confirmation expected |
| After confirmation | Pending key promoted to active |

---

## Key Takeaway

The debug demonstrates a two-stage key rotation model:

1. Generate and install a new QKD key on both routers.
2. Schedule activation in the future.
3. Keep traffic flowing on the old key.
4. Allow MKA to migrate naturally.
5. Promote the new key only after MKA confirms it.

In the captured logs:

- The CA1 link uses a rotation delay of roughly **6 minutes**.
- The CA9 link uses a rotation delay of roughly **5 minutes**.
- A Junos commit occurs on both routers every time a new key is installed.
- MKA confirmation is the event that officially completes a rotation.
