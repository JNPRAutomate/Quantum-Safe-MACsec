root@freebsd11:~ # freebsd-version
11.2-RELEASE
root@freebsd11:~ # uname -a 
FreeBSD freebsd11 11.2-RELEASE FreeBSD 11.2-RELEASE #0 r335510: Fri Jun 22 04:32:14 UTC 2018     root@releng2.nyi.freebsd.org:/usr/obj/usr/src/sys/GENERIC  amd64
root@freebsd11:~ #   



Se ora sei root sulla macchina Ubuntu, esegui:

usermod -aG sudo aterren
Poi abilita sudo passwordless per aterren:
echo 'aterren ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/aterren
chmod 440 /etc/sudoers.d/aterren
visudo -cf /etc/sudoers.d/aterren

Deve rispondere qualcosa tipo:
/etc/sudoers.d/aterren: parsed OK

Poi testa:
su - aterren
sudo -n true
echo $?

Se stampa:
0

sei a posto.