Script initially: 

CA double-buffer
-> interface commit
-> MACsec flap

Now:
authentication key-chain
    -> hitless rollover
    -> no interface commit

Today your script is effectively programming: security authentication-key-chains which is configuration.

Option 1 - Current approach
fetch key
update key-chain
commit
Option 2 - Commit only when key pool depleted
instead of commit every minute
we do
fetch 10 keys
install 10 keys
commit once
then: 
start-time
17:00
17:01
17:02
17:03
...
17:09
MKA naturally moves through them. --> commit every 10 minutes instead of every 60 deconds

Option 3 - Runtime key injection

so with the script move from 1 key 1 commit to 10 keys 1 commit.
with start time pre programmed. the mka does the hitless roll over 
this gives you 
5-minute rotation
1-minute rekeys
1 commit every 5 minutes
or 10 minutes horizon with 1 commit every 10 minutes 
same architecture. 

does your current qkd_onbox.py commit once per generated key, or can it preload N future keys in the same key-chain and let MKA consume them using their start-time values?

# on the master
run_master()
    -> install_keychain_key()
        -> commit
``
# during bootstrap
bootstrap_keychain_link()
    -> install_keychain_key()
        -> commit

# for one rotation cycle
master sends install-key to peer
peer runs install_keychain_key()  -> commit on peer
master runs install_keychain_key() -> commit on master

So yes: one rotation means one commit on each side.

# effective behaviour: 
script runs every 60 seconds
if pending key exists and is not due -> skip
when pending key becomes active -> promote
then schedule another future key
that scheduling causes a commit on both routers

So it is not blindly committing every 60 seconds, but every new QKD key installation still commits.

# improvement! 
Do not install 1 key per commit.
Install N future keys in one commit.
key_batch_size = 5
interval_seconds = 60
instead of 
T+00 fetch key 1 -> commit
T+60 fetch key 2 -> commit
T+120 fetch key 3 -> commit
T+180 fetch key 4 -> commit
T+240 fetch key 5 -> commit

you do: 
T+00 fetch key 1,2,3,4,5
     install all with future start-times
     commit once

key 0 start-time 2026-07-11.07:10
key 1 start-time 2026-07-11.07:11
key 2 start-time 2026-07-11.07:12
key 3 start-time 2026-07-11.07:13
key 4 start-time 2026-07-11.07:14

# changes
* Bootstrap:
  * install initial batch of 5 keys
  * commit once on peer
  * commit once on master

* Normal script run every 60 seconds:
  * check MKA current CKN
  * update local JSON state
  * if enough future keys already installed:
      * no commit
  * if future key horizon is low:
      * fetch next batch
      * install next batch
      * commit once

event-script frequency: 60 seconds
key rotation interval: 60 seconds
key batch size: 5
commit frequency: roughly every 5 rotations

