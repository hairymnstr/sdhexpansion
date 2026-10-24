import vfs
import app
import settings
import sys
from machine import SDCard
from system.hexpansion.config import HexpansionConfig
from system.eventbus import eventbus
from system.hexpansion.events import HexpansionAppLauncherAddEvent
from system.launcher.app import InstallNotificationEvent
from system.launcher.app import AppDirAddedNotificationEvent, AppDirRemovedNotificationEvent
from app_components import Menu, Notification, clear_background
from system.scheduler.events import RequestForegroundPushEvent

# default location to mount the main flash device
MOUNTPATH = "/sdcard"
APPDIR = "/sdcard_apps"

# menu item text for the actions, the last two are toggles whose labels show their state
main_menu_items = ["Mount", "Remove", "Format"]
AUTOMOUNT_LABEL = "Auto mount: "
QUIET_LABEL = "Quiet start: "
# keys for the persistent badge settings
AUTOMOUNT_KEY = "sdhexp_automount"
QUIET_KEY = "sdhexp_quiet"

class SDHexpApp(app.App):
    def __init__(self, config=None):
        # if this is run from the EEPROM then config will be a HexpansionConfig for whatever slot it's plugged into
        # if not then we assume it's plugged into slot 2 (right hand side) for testing the app
        if config == None:
            config = HexpansionConfig(2)
        self.config = config

        # user preferences, kept in the badge settings so they survive a reboot
        self.automount = bool(settings.get(AUTOMOUNT_KEY, False))
        self.quiet = bool(settings.get(QUIET_KEY, False))

        # Make a menu GUI widget with the mount, unmount, format and preference options
        items = main_menu_items + [self.toggle_label(AUTOMOUNT_LABEL, self.automount), self.toggle_label(QUIET_LABEL, self.quiet)]
        self.menu = Menu(self, items, select_handler=self.select_handler, back_handler=self.back_handler)

        # create a handle for the notification
        self.notification = None

        # create a flag to request foreground exactly once
        self.foregrounded = False

        # set up the power control pin, setting this to low (off) disables the card
        # this can be used to reset the card if it got into a bad state
        self.config.ls_pin[0].init(self.config.ls_pin[0].OUT)
        self.config.ls_pin[0].on()

        # set up SD Card in SPI mode
        # Slot 3 uses SPI1 which isn't the one driving the screen so should be free
        self.sd = SDCard(slot=3, sck=config.pin[2], cs=config.pin[0], mosi=config.pin[1], miso=config.pin[3])

        self.mounted = False

        # the automount is done from update() once the app is running
        self.automount_pending = self.automount

        # list the app in the launcher so it can still be opened when it starts quietly
        eventbus.emit(HexpansionAppLauncherAddEvent(self.config.port, "SD Card"))

        # run the app class init
        super().__init__()

    def deinit(self):
        if self.mounted:
            vfs.umount(MOUNTPATH)
            if MOUNTPATH in sys.path:
                del sys.path[sys.path.index(MOUNTPATH)]
            eventbus.emit(AppDirRemovedNotificationEvent(MOUNTPATH+APPDIR))
            self.mounted = False
        self.sd.deinit()

    def toggle_label(self, label, value):
        return label + ("On" if value else "Off")

    def mount(self):
        # mounts the device at the default location
        vfs.mount(self.sd, MOUNTPATH)
        sys.path.append(MOUNTPATH)
        eventbus.emit(AppDirAddedNotificationEvent(MOUNTPATH+APPDIR))
        self.mounted = True

    def toggle(self, key, label, value, item_idx):
        # flip a preference, store it and update the menu text
        value = not value
        settings.set(key, value)
        settings.save()
        self.menu.menu_items[item_idx] = self.toggle_label(label, value)
        return value

    def select_handler(self, item, item_idx):
        # menu handler we try catch this whole thing to keep the code small for embedding in the EEPROM
        try:
            if item == "Mount":
                self.mount()
            elif item == "Remove":
                # unmounts (safely removes) the storage
                self.deinit()
            elif item == "Format":
                # Format the drive as FAT
                vfs.VfsFat.mkfs(self.sd)
            elif item.startswith(AUTOMOUNT_LABEL):
                self.automount = self.toggle(AUTOMOUNT_KEY, AUTOMOUNT_LABEL, self.automount, item_idx)
                item = "Auto mount"
            elif item.startswith(QUIET_LABEL):
                self.quiet = self.toggle(QUIET_KEY, QUIET_LABEL, self.quiet, item_idx)
                item = "Quiet start"
            # if the try-catch hasn't jumped out yet then what we tried to do must have succeded
            self.notification = Notification(f"{item} succeded")
        except Exception as err:
            # let the user know something went wrong
            self.notification = Notification(f"{item} failed {err}")

    def back_handler(self):
        # no state is saved in the app just minimise when the user is done with their choices
        self.minimise()

    # ctx GUI stuff just taken from the examples
    def draw(self, ctx):
        clear_background(ctx)
        self.menu.draw(ctx)
        if self.notification:
            self.notification.draw(ctx)

    def update(self, delta):
        failed = False
        if self.automount_pending:
            self.automount_pending = False
            try:
                self.mount()
            except Exception as err:
                # a failed automount is worth interrupting for, even in quiet mode
                failed = True
                self.notification = Notification(f"Mount failed {err}")
        if not self.foregrounded: # Bring the app to the foreground on first run unless quiet
            if failed or not self.quiet:
                eventbus.emit(RequestForegroundPushEvent(self))
            self.foregrounded = True

        self.menu.update(delta)
        if self.notification:
            self.notification.update(delta)

__app_export__ = SDHexpApp
