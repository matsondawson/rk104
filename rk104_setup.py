"""Work out why the RK104 can't be reached and which setup command fixes it."""
import grp, os, pwd
from dataclasses import dataclass

import rk104

RULE_FILE = "/etc/udev/rules.d/70-rk104.rules"
MATCH = 'KERNEL=="hidraw*", ATTRS{idVendor}=="0c45", ATTRS{idProduct}=="8006", MODE="0660"'
GROUP = "rk104"


@dataclass
class Advice:
    title: str
    text: str           # plain-text explanation (may contain simple HTML)
    command: str = ""   # shell snippet to show / copy; empty = nothing to run


def os_release():
    info = {}
    try:
        with open("/etc/os-release") as fh:
            for line in fh:
                k, _, v = line.strip().partition("=")
                if k:
                    info[k] = v.strip('"')
    except OSError:
        pass
    return info


def has_logind():
    """systemd-logind (or elogind) is managing seats, so TAG+="uaccess" works."""
    return os.path.isdir("/run/systemd/seats")


def _user():
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return os.environ.get("USER", "$USER")


def _in_group(name):
    try:
        g = grp.getgrnam(name)
    except KeyError:
        return False
    return g.gr_gid in os.getgroups()


def _group_configured(name):
    """User is listed in the group in /etc/group (may need re-login to take effect)."""
    try:
        return _user() in grp.getgrnam(name).gr_mem
    except KeyError:
        return False


def _distro_name(info):
    return info.get("PRETTY_NAME") or info.get("NAME") or "Linux"


def diagnose(err):
    """Return Advice for a rk104.DeviceError."""
    info = os_release()
    distro = _distro_name(info)

    if isinstance(err, rk104.DeviceNotFound):
        if rk104.DONGLE in rk104.present_ids():
            return Advice(
                "Keyboard is in 2.4G wireless mode",
                "The RK104's 2.4G receiver is plugged in, but this app only supports the "
                "<b>wired USB</b> connection.<br><br>Connect the keyboard with its USB cable and "
                "switch it to wired mode, then press Retry.")
        return Advice(
            "Keyboard not found",
            "No RAGNOK RK104 (USB ID <tt>0c45:8006</tt>) is connected.<br><br>"
            "Plug the keyboard in with its USB cable (wired mode), then press Retry.",
            "lsusb | grep -i 0c45:8006   # should list the keyboard")

    if not isinstance(err, rk104.DevicePermission):
        return Advice("Can't talk to the keyboard", str(err))

    path = err.path
    rule_present = os.path.exists(RULE_FILE)
    ident = info.get("ID", "")

    # NixOS: /etc is generated, rules go in configuration.nix
    if ident == "nixos":
        return Advice(
            "USB permission needed (NixOS)",
            "Your user can't open <tt>%s</tt>. Add this to <tt>configuration.nix</tt>, run "
            "<tt>sudo nixos-rebuild switch</tt>, then unplug and replug the keyboard." % path,
            "services.udev.extraRules = ''\n  %s, TAG+=\"uaccess\"\n'';" % MATCH)

    if has_logind():
        if rule_present:
            return Advice(
                "Rule installed but not active yet",
                "<tt>%s</tt> exists, but <tt>%s</tt> is still not accessible.<br><br>"
                "Unplug and replug the keyboard (or run the command below), then press Retry. "
                "If it still fails, check the rule file matches the one in the README." % (RULE_FILE, path),
                "sudo udevadm control --reload && sudo udevadm trigger")
        return Advice(
            "USB permission needed",
            "Detected <b>%s</b> with systemd-logind. Your user can't open <tt>%s</tt>.<br><br>"
            "Run this once in a terminal (it asks for your password), then press Retry:" % (distro, path),
            "echo '%s, TAG+=\"uaccess\"' \\\n  | sudo tee %s\n"
            "sudo udevadm control --reload && sudo udevadm trigger" % (MATCH, RULE_FILE))

    # No logind: group-based access.
    user = _user()
    if rule_present and _group_configured(GROUP) and not _in_group(GROUP):
        return Advice(
            "Log out and back in",
            "You were added to the <tt>%s</tt> group, but this session started before that. "
            "Log out and back in (or reboot), then start the app again." % GROUP)
    if ident == "alpine":  # busybox: addgroup instead of groupadd/usermod
        group_cmds = "sudo addgroup %s\nsudo addgroup %s %s\n" % (GROUP, user, GROUP)
        reload_cmd = "sudo udevadm trigger   # with mdev instead of eudev: replug or reboot"
    else:
        group_cmds = "sudo groupadd -f %s\nsudo usermod -aG %s %s\n" % (GROUP, GROUP, user)
        reload_cmd = "sudo udevadm control --reload && sudo udevadm trigger"
    return Advice(
        "USB permission needed",
        "Detected <b>%s</b> without systemd-logind, so access is granted through a "
        "<tt>%s</tt> group. Your user can't open <tt>%s</tt>.<br><br>"
        "Run this once in a terminal, then <b>log out and back in</b>:" % (distro, GROUP, path),
        "%secho '%s, GROUP=\"%s\"' \\\n  | sudo tee %s\n%s"
        % (group_cmds, MATCH, GROUP, RULE_FILE, reload_cmd))
