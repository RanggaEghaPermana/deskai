# What the package's own scripts share. Everything DeskAI sets up belongs to a user and is done as that user, so the
# package reaches whoever's at the desktop right now. It's sourced, with nothing to run by itself.

# Runs a command as users who are logged in, in their sessions' services: `in_sessions desktop command...` for the
# person at the screen right now, or `in_sessions everyone command...` for all of them, once each, however they're logged in.
# Never fails, since nothing here may stand in the way of installing or removing the package.
in_sessions() {
  who="$1"
  shift
  command -v loginctl >/dev/null 2>&1 || return 0
  seen=" "
  for session in $(loginctl list-sessions --no-legend 2>/dev/null | awk '{print $1}'); do
    info="$(loginctl show-session "$session" -p Name -p Active -p Type -p Class 2>/dev/null)" || continue
    case "$info" in *Class=user*) ;; *) continue ;; esac
    if [ "$who" = desktop ]; then
      case "$info" in *Active=yes*) ;; *) continue ;; esac
      case "$info" in *Type=wayland*|*Type=x11*) ;; *) continue ;; esac
    fi
    name="$(printf '%s\n' "$info" | sed -n 's/^Name=//p')"
    case "$seen" in *" $name "*) continue ;; esac
    seen="$seen$name "
    uid="$(id -u "$name" 2>/dev/null)" || continue
    runuser -u "$name" -- env XDG_RUNTIME_DIR="/run/user/$uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" "$@" >/dev/null 2>&1 || true
  done
  return 0
}
