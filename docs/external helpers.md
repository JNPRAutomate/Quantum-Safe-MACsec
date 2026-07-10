Sì, Andrea. Con il tree attuale io non creerei subito un nuovo file tipofs_utils.py. Hai già 12 moduli in lib/, abbastanza per ora. La cosa giusta è: prima stabilizzi, poi eventualmente estrai helper comuni quando hai duplicazione reale e stabile.

Il tuo tree ora è già abbastanza pulito:

lib/
├── clean.py
├── config.py
├── identity.py
├── inventory_builder.py
├── kme_instructions.py
├── logger.py
├── onbox_builder.py
├── pki_hierarchical.py
├── pki_self_signed.py
├── provisioning.py
├── rendering.py
└── settings.py

Decisione pratica: niente nuovo file adesso

Per il problema immediato di:

def write_file(path, data):
    with open(path, "wb") as f:
        f.write(data)


io non creerei fs_utils.py adesso. Lo fixerei localmente dentro pki_self_signed.py, perché al momento è il modulo che sta fallendo mentre genera certs/self_signed/offbox_rootCA.crt. In pki_self_signed.py vedo già write_file() e build_self_signed_pki() come funzioni interne al builder self-signed, quindi la patch minima è coerente e a basso rischio.

Sostituisci in pki_self_signed.py:

def write_file(path, data):
    with open(path, "wb") as f:
        f.write(data)


con:

def write_file(path, data, mode=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "wb") as f:
        f.write(data)

    if mode is not None:
        path.chmod(mode)

    return path


Questo risolve subito:

FileNotFoundError: certs/self_signed/offbox_rootCA.crt


senza creare nuovi moduli.

Cosa potrebbe essere esternalizzato, ma NON subito
1. Helper filesystem / crypto-write

In pki_hierarchical.py hai già funzioni molto simili e più mature:

ensure_dir()
write_private_key()
write_certificate()
write_bytes()
load_private_key()
load_certificate()


Queste sono effettivamente candidate a un file comune tipo pki_common.py o crypto_io.py, perché sono utility riusabili tra self-signed e hierarchical.

Però non lo farei adesso. Motivo: pki_hierarchical.py è già complesso e ha una sua logica completa di root CA, issuing CA, leaf cert, trust exchange e verifica chain; spostare ora helper crypto comuni rischia di introdurre bug mentre stai ancora stabilizzando create --inventory.

Quindi il mio consiglio è:

Adesso:
    patch locale in pki_self_signed.py

Dopo che self_signed e hierarchical_ca funzionano:
    valutare lib/pki_common.py

2. Istruzioni manuali KME

Qui hai fatto bene a creare kme_instructions.py. Questa funzione non appartiene né a pki_self_signed.py né a pki_hierarchical.py, perché non genera certificati: stampa istruzioni operative per copiare i certificati verso il KME. Il file contiene già la funzione per le istruzioni manuali dei profili self_signed e hierarchical_ca, quindi come responsabilità è corretto che esista separato.

Questa separazione è giusta:

pki_self_signed.py      -> genera self-signed PKI
pki_hierarchical.py     -> genera hierarchical CA PKI
kme_instructions.py     -> stampa istruzioni manuali KME
kme_orchestrator.py     -> copia/installa sul KME

3. Config loader e path resolver

config.py è già il punto corretto per:

load_yaml()
load_inventory_base()
load_runtime_devices()
load_runtime_topology()
load_runtime_pki_profile()
load_inventory_file()
load_inventory()
load_platform()
resolve_inventory()


Queste funzioni sono direttamente coerenti con il ruolo di config.py: leggere file YAML e risolvere path di inventory/runtime/platform.

Io non aggiungerei lì funzioni come write_bytes() owrite_file(), perché mischierebbe config loading e filesystem writing. Se vuoi minimizzare file, meglio lasciare write_file() in pki_self_signed.py per ora, invece di sporcare config.py.

4. Identity helpers

identity.py oggi contiene più categorie di funzioni:

helper base, es. qkd_script_user(), qkd_deploy_user(), qkd_ssh_home()
normalizzazione device, es. normalize_device(), normalize_devices(), device_name()
esecuzione comandi, es. pyez_shell_cmd(), ssh_deploy_cmd(), ssh_cmd()
preflight vero e proprio, es. preflight_all_devices() e vari check pre/post deploy.

Questo è un modulo che in futuro potresti separare in:

identity.py        -> policy e preflight identity
device_model.py    -> normalize_device, device_name, platform_name
junos_exec.py      -> ssh_cmd, pyez_shell_cmd, ssh_deploy_cmd


Ma anche qui: non adesso. Questo è un modulo centrale e funzionante; meglio non fare refactoring ampio mentre stai chiudendo PKI/create/deploy.

5. Inventory/topology helpers

inventory_builder.py contiene funzioni distinte:

generate_macsec_keys()
assign_links()
build_inventory()
build_pairs()
build_runtime_pki_profile()
build_full_inventory()


Queste sono tutte nello stesso dominio: costruzione runtime inventory e topology. Quindi per ora le lascerei lì.

Solo più avanti potresti eventualmente separare:

topology.py          -> build_pairs, assign_links
inventory_builder.py -> build_inventory, build_full_inventory


Ma ora non vale la pena.

6. Rendering e provisioning

rendering.py è chiaramente specializzato in Jinja rendering e build_device_config(), quindi è nel posto giusto.

provisioning.py invece contiene molte funzioni di deploy vero e proprio, fra cui rendering comune, script config, remote_file_exists(), build_macsec_static(), push_certs(), push_config(), resolve_peers() e run_provisioning().

Anche qui: un domani si potrebbe splittare in:

provisioning.py
cert_deploy.py
junos_config_push.py


ma al momento non creerei nuovi file, perché questa parte è legata al deploy e la priorità è far passare il workflow completo.

7. Onbox builder

onbox_builder.py è ora pulito come responsabilità:

build_onbox_config()
generate_onbox_script()
build_onbox_artifacts()


Quindi resta così. Il suo compito è esattamente generare i qkd_onbox.py runtime per device.

8. Clean e logger

clean.py è già coerente: runtime cleanup, cert cleanup, remote cleanup candidates e handle_clean().

logger.py va bene da solo: ha solo setup_logger(). Non lo toccherei.

Mappa finale consigliata
Da tenere come sono ora
settings.py
config.py
logger.py
rendering.py
onbox_builder.py
inventory_builder.py
clean.py
kme_instructions.py

Da non toccare finché non hai deploy stabile
identity.py
provisioning.py
pki_hierarchical.py

Da patchare subito ma senza creare nuovi file
pki_self_signed.py


Patch immediata:

def write_file(path, data, mode=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "wb") as f:
        f.write(data)

    if mode is not None:
        path.chmod(mode)

    return path

Se più avanti vorrai davvero estrarre funzioni comuni

Solo dopo aver chiuso i test self_signed e hierarchical_ca, l’unico nuovo file che avrebbe davvero senso è:

lib/pki_common.py


Lì sposterei solo roba PKI comune:

ensure_dir()
write_bytes()
write_private_key()
write_certificate()
load_private_key()
load_certificate()
generate_key()
hash_algorithm()
cert_validity()


Però non adesso. Ora il refactoring giusto è piccolo:

fix pki_self_signed write_file
run create
verify certs/self_signed generation
commit

Prossimo comando pratico

Dopo la patch di write_file():

python3 -m compileall .
rm -rf certs
python3 qkd_orchestrator.py create --inventory ring_5_acx


Se dopo questo self-signed passa, solo allora andiamo sul test hierarchical.