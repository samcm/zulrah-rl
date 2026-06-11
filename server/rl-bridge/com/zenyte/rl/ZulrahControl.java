package com.zenyte.rl;

import com.google.common.eventbus.Subscribe;
import com.zenyte.game.content.boss.zulrah.ZulrahInstance;
import com.zenyte.game.content.boss.zulrah.ZulrahNPC;
import com.zenyte.game.content.consumables.Consumable;
import com.zenyte.game.content.skills.prayer.Prayer;
import com.zenyte.game.item.Item;
import com.zenyte.game.packet.Session;
import com.zenyte.game.world.entity.player.Action;
import com.zenyte.game.world.entity.player.action.combat.MagicCombat;
import com.zenyte.game.world.entity.player.action.combat.PlayerCombat;
import com.zenyte.game.world.entity.player.container.impl.equipment.Equipment;
import com.zenyte.game.world.entity.player.container.impl.equipment.EquipmentSlot;
import com.zenyte.game.tasks.WorldTask;
import com.zenyte.game.tasks.WorldTasksManager;
import com.zenyte.game.ui.PaneType;
import com.zenyte.game.world.World;
import com.zenyte.game.world.entity.player.Device;
import com.zenyte.game.world.entity.player.Player;
import com.zenyte.game.world.entity.player.PlayerInformation;
import com.zenyte.game.world.entity.player.Skills;
import com.zenyte.network.login.codec.LoginDecoder;
import com.zenyte.network.login.packet.LoginPacketIn;
import com.zenyte.network.login.packet.inc.LoginType;
import com.zenyte.plugins.events.ServerLaunchEvent;
import io.netty.channel.embedded.EmbeddedChannel;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentLinkedQueue;

/**
 * The RL control hook. Bridges the {@link ControlServer} (off-thread connections) to the world thread:
 *
 *  - On {@link ServerLaunchEvent} it starts the control server and schedules a per-tick {@link WorldTask}.
 *  - Connection threads {@link #submit(Job)} jobs; the per-tick {@link #tick()} drains and executes them ON the world
 *    thread (Player/NPC state is not thread-safe off it).
 *  - A {@code step} action is applied this tick and its observation is returned at the start of the next tick, so each
 *    step spans exactly one game tick. A {@code reset} launches a Zulrah instance and completes once Zulrah spawns.
 *
 * Headless bot players are created via {@link #createBot(String)} (login bypass over a {@link BotChannel}); they are
 * kept alive each tick by refreshing {@code lastReceivedPacket} and draining buffered outbound packets.
 *
 * Action ids (discrete; attack is decoupled from weapon-switching so the policy learns when to switch styles):
 *   0 attack        1 equip range   2 equip mage   3 protect magic   4 protect missiles
 *   5 eat           6 antivenom     7 restore prayer
 *   8 move N        9 move E        10 move S      11 move W   12 drop prayer (flick off)
 */
public final class ZulrahControl {

    static final class Job {
        final Bot bot;
        final char type; // 'R' reset, 'S' step, 'Q' state
        final int arg;
        final CompletableFuture<String> future;
        int waited;
        boolean launched;

        Job(final Bot bot, final char type, final int arg, final CompletableFuture<String> future) {
            this.bot = bot;
            this.type = type;
            this.arg = arg;
            this.future = future;
        }
    }

    private static final int XP_99 = 13_034_431;   // 99 combat stats: HP buffer + DPS so the agent can survive fumbles
                                                   // long enough to land kills and bootstrap (prayer is the real defence)

    // Mid-tier Zulrah loadout (Karil's barrows ranged / Ahrim's barrows magic). Not best-in-slot, but enough survivability
    // + damage that a decent policy can win and learn. Protect prayers fully block regardless of armour, so prayer-switching
    // remains the core skill. Rune crossbow + bolts is the no-charge ranged option; trident is the mage weapon (pre-charged).
    private static final int CROSSBOW = 9185, BOLTS = 9144, TRIDENT = 11905, TRIDENT_CHARGES = 2500;
    private static final int SHARK = 385, BREW = 6685, ANTIVENOM = 12913, SUPER_RESTORE = 3024;
    private static final int N_BOLTS = 5000, N_SHARK = 8, N_BREW = 4, N_ANTIVENOM = 3, N_PRAYER = 4;

    // Zulrah arena template bounds (for the viewer's collision map; matches env/state.py).
    private static final int ARENA_X0 = 2256, ARENA_X1 = 2277, ARENA_Y0 = 3062, ARENA_Y1 = 3080;

    private static final ConcurrentLinkedQueue<Job> QUEUE = new ConcurrentLinkedQueue<>();
    private static final ConcurrentLinkedQueue<Bot> REMOVALS = new ConcurrentLinkedQueue<>();
    private static final List<Bot> BOTS = new ArrayList<>();
    private static final List<Job> DEFERRED = new ArrayList<>(); // steps awaiting next-tick snapshot
    private static final List<Job> PENDING_RESETS = new ArrayList<>(); // resets awaiting Zulrah spawn

    private static long tickCount;

    @Subscribe
    public static void onServerLaunch(final ServerLaunchEvent event) {
        ControlServer.start();
        WorldTasksManager.schedule(new WorldTask() {
            @Override
            public void run() {
                tick();
            }
        }, 0, 0);
        System.out.println("[RL] ZulrahControl initialised; per-tick hook scheduled");
    }

    static void submit(final Job job) {
        QUEUE.add(job);
    }

    /** Number of live RL bots (one per gym env / control connection). For the spectate command. */
    public static int botCount() {
        return BOTS.size();
    }

    /** The bot at the given index, or {@code null} if out of range. For the spectate command. */
    public static Bot getBot(final int index) {
        return index >= 0 && index < BOTS.size() ? BOTS.get(index) : null;
    }

    /** A snapshot list of the live bots. For the spectate command. */
    public static List<Bot> getBots() {
        return new ArrayList<>(BOTS);
    }

    static void dispose(final Bot bot) {
        REMOVALS.add(bot);
    }

    /** Runs once per game tick on the world thread. */
    static void tick() {
        tickCount++;

        // Keep bots alive (anti-logout) and drain their buffered outbound packets.
        for (final Bot bot : BOTS) {
            final Player player = bot.player;
            if (player == null) {
                continue;
            }
            player.setLastReceivedPacket(System.currentTimeMillis());
            // Zulrah's mage/range/flicking attacks abort (isCancelled) while the target is "teleported" — the faithful
            // way a real player teleporting out of the fight stops the boss. A headless bot has no client driving its
            // per-tick movement processing, so the flag stays stuck true from the spawn teleport-in and Zulrah never
            // attacks an idle bot. Clear it each tick: the bot isn't actually teleporting, so it should be attacked.
            player.setTeleported(false);
            try {
                ((EmbeddedChannel) player.getSession().getChannel()).releaseOutbound();
            } catch (final Exception ignored) {
                // never let a bot's packet bookkeeping break the world tick
            }
        }

        // Complete step snapshots deferred from the previous tick (the action has now been processed).
        if (!DEFERRED.isEmpty()) {
            for (final Job job : DEFERRED) {
                job.future.complete(snapshot(job.bot));
            }
            DEFERRED.clear();
        }

        // Resolve resets: first wait for the bot to be idle (not mid-death) and launch, then wait for Zulrah to spawn.
        if (!PENDING_RESETS.isEmpty()) {
            final Iterator<Job> it = PENDING_RESETS.iterator();
            while (it.hasNext()) {
                final Job job = it.next();
                final Bot bot = job.bot;
                final Player player = bot.player;
                if (!job.launched) {
                    // A just-ended episode's death sequence teleports the player out a few ticks later; launching the
                    // new instance before that fires would yank the bot straight back out. Wait until it's idle.
                    if (player != null && (!player.isLocked() || ++job.waited > 50)) {
                        final ZulrahInstance old = bot.instance;
                        if (old != null) {
                            old.destroyRegion();
                        }
                        restoreLoadout(player);
                        bot.instance = ZulrahInstance.launchHeadless(player);
                        job.launched = true;
                        job.waited = 0;
                    }
                    continue;
                }
                final ZulrahInstance instance = bot.instance;
                final boolean ready = instance != null && instance.getZulrah() != null;
                if (ready || ++job.waited > 40) {
                    // Curriculum: start Zulrah at a reduced HP so a kill is reachable from a cold policy. job.arg is the
                    // requested starting HP (0 = full); set it before the first observation so the agent sees it.
                    if (ready && job.arg > 0) {
                        final ZulrahNPC zulrah = instance.getZulrah();
                        zulrah.setHitpoints(Math.min(job.arg, zulrah.getMaxHitpoints()));
                    }
                    job.future.complete(snapshot(bot));
                    it.remove();
                }
            }
        }

        // Process newly submitted jobs.
        Job job;
        while ((job = QUEUE.poll()) != null) {
            try {
                switch (job.type) {
                    case 'R':
                        handleReset(job);
                        break;
                    case 'S':
                        applyAction(job.bot, job.arg);
                        DEFERRED.add(job);
                        break;
                    case 'Q':
                        job.future.complete(snapshot(job.bot));
                        break;
                    case 'M':
                        job.future.complete(buildMap(job.bot));
                        break;
                    default:
                        job.future.complete("{\"error\":\"bad job\"}");
                }
            } catch (final Exception e) {
                e.printStackTrace();
                job.future.complete("{\"error\":\"" + ControlServer.escape(e.toString()) + "\"}");
            }
        }

        // Remove disposed bots.
        Bot bot;
        while ((bot = REMOVALS.poll()) != null) {
            final Player player = bot.player;
            if (player != null) {
                try {
                    World.unregisterPlayer(player);
                } catch (final Exception ignored) {
                }
            }
            BOTS.remove(bot);
        }
    }

    private static void handleReset(final Job job) {
        final Bot bot = job.bot;
        if (bot.player == null) {
            bot.player = createBot(bot.name);
            BOTS.add(bot);
        }
        // Defer the actual instance launch to the PENDING_RESETS loop, which waits until the bot is idle.
        job.waited = 0;
        job.launched = false;
        PENDING_RESETS.add(job);
    }

    /** Creates a headless, world-registered Player with no real client (login bypass over a {@link BotChannel}). */
    private static Player createBot(final String name) {
        final BotChannel channel = new BotChannel();
        final LoginPacketIn login = new LoginPacketIn(
                LoginType.NEW_LOGIN_CONNECTION, 0, 0, name, "bot", 0,
                new int[18], "", 0, 0, LoginDecoder.AuthType.TRUSTED_COMPUTER,
                null, null, 0, new int[4], new int[4], "00:00:00:00:00:00", Device.DESKTOP);
        final Session session = new Session(channel, login);
        final PlayerInformation info = new PlayerInformation(session, login);
        final Player player = new Player(info, null);
        session.setPlayer(player);
        player.setDefaultSettings();
        World.addPlayer(player);
        player.loadMapRegions();
        // setRunning = the player is live/processed each tick (NOT setRun, which is the walk/run movement toggle).
        player.setRunning(true);
        // A real client triggers pane construction; a headless bot must set one or onLogin's interface sends NPE.
        player.getInterfaceHandler().setPane(PaneType.RESIZABLE);
        player.onLogin();
        // Offline-dev leaves combat_xp_rate at the invalid default of 1, which makes addXp() throw mid-hit and abort
        // the attack before it lands. Set a valid rate (50) so the bot's hits actually apply.
        player.setExperienceMultiplier(50, 50);
        player.setLastReceivedPacket(System.currentTimeMillis());
        System.out.println("[RL] bot created: " + name);
        return player;
    }

    private static void restoreLoadout(final Player player) {
        try {
            // 99 in every combat skill WITH xp, so getMaxHitpoints() (xp-driven) returns 99.
            for (int skill = 0; skill <= 6; skill++) {
                player.getSkills().setSkill(skill, 99, XP_99);
            }
            player.setHitpoints(player.getMaxHitpoints());
            player.getPrayerManager().setPrayerPoints(99);
            player.getToxins().reset();
            player.getPrayerManager().deactivateActivePrayers();
            player.getTemporaryAttributes().remove("rl_last_hit_type"); // clear last-attack memory each episode
            player.getTemporaryAttributes().remove("rl_last_hit_cycle");

            equipRange(player); // default style; the policy switches presets with equip range/mage

            final var inv = player.getInventory();
            inv.clear();
            inv.addItem(SHARK, N_SHARK);
            inv.addItem(BREW, N_BREW);
            inv.addItem(ANTIVENOM, N_ANTIVENOM);
            inv.addItem(SUPER_RESTORE, N_PRAYER);
        } catch (final Exception e) {
            e.printStackTrace();
        }
    }

    /** Mid-tier ranged preset (Karil's barrows + rune crossbow). Switching presets is a learnable action, not a freebie. */
    private static void equipRange(final Player player) {
        final Equipment eq = player.getEquipment();
        eq.set(EquipmentSlot.HELMET, new Item(4732));     // karil's coif
        eq.set(EquipmentSlot.AMULET, new Item(1704));     // amulet of glory
        eq.set(EquipmentSlot.PLATE, new Item(4736));      // karil's leathertop
        eq.set(EquipmentSlot.LEGS, new Item(4738));       // karil's leatherskirt
        eq.set(EquipmentSlot.HANDS, new Item(1059));      // leather gloves
        eq.set(EquipmentSlot.BOOTS, new Item(1061));      // leather boots
        eq.set(EquipmentSlot.SHIELD, null);               // 1h crossbow leaves shield empty (no leftover mage book)
        eq.set(EquipmentSlot.AMMUNITION, new Item(BOLTS, N_BOLTS));
        eq.set(EquipmentSlot.WEAPON, new Item(CROSSBOW));
        eq.refresh();
        player.getBonuses().update();   // raw set() bypasses bonus recompute; Zulrah's accuracy vs the bot needs it
    }

    /** Mid-tier magic preset (Ahrim's barrows + trident). */
    private static void equipMage(final Player player) {
        final Equipment eq = player.getEquipment();
        eq.set(EquipmentSlot.HELMET, new Item(4708));     // ahrim's hood
        eq.set(EquipmentSlot.AMULET, new Item(1727));     // amulet of magic
        eq.set(EquipmentSlot.PLATE, new Item(4712));      // ahrim's robetop
        eq.set(EquipmentSlot.LEGS, new Item(4714));       // ahrim's robeskirt
        eq.set(EquipmentSlot.HANDS, new Item(1059));      // leather gloves
        eq.set(EquipmentSlot.BOOTS, new Item(4097));      // mystic boots
        eq.set(EquipmentSlot.SHIELD, null);
        eq.set(EquipmentSlot.WEAPON, new Item(TRIDENT, 1, TRIDENT_CHARGES));
        eq.refresh();
        player.getBonuses().update();
    }

    private static void applyAction(final Bot bot, final int action) {
        final Player player = bot.player;
        if (player == null) {
            return;
        }
        switch (action) {
            case 0: // attack Zulrah with the currently equipped weapon
                attack(bot);
                break;
            case 1: // equip ranged preset (crossbow + range armour)
                equipRange(player);
                break;
            case 2: // equip magic preset (trident + ancestral)
                equipMage(player);
                break;
            case 3:
                setOverhead(player, Prayer.PROTECT_FROM_MAGIC);
                break;
            case 4:
                setOverhead(player, Prayer.PROTECT_FROM_MISSILES);
                break;
            case 5: // eat
                if (!consume(player, SHARK)) {
                    consume(player, BREW);
                }
                break;
            case 6: // drink antivenom+
                consume(player, ANTIVENOM);
                break;
            case 7: // drink super restore (prayer + stats)
                consume(player, SUPER_RESTORE);
                break;
            case 8:
                player.addWalkSteps(player.getX(), player.getY() + 1, 1, true);
                break;
            case 9:
                player.addWalkSteps(player.getX() + 1, player.getY(), 1, true);
                break;
            case 10:
                player.addWalkSteps(player.getX(), player.getY() - 1, 1, true);
                break;
            case 11:
                player.addWalkSteps(player.getX() - 1, player.getY(), 1, true);
                break;
            case 12: // drop all overhead prayers (enables prayer flicking)
                setOverhead(player, null);
                break;
            default:
                break;
        }
    }

    private static void attack(final Bot bot) {
        final Player player = bot.player;
        final ZulrahNPC zulrah = bot.instance == null ? null : bot.instance.getZulrah();
        if (zulrah == null || zulrah.isDead()) {
            return;
        }
        // "attack" maintains an auto-attack on Zulrah, exactly as in RS: re-clicking your current target does NOT
        // restart your weapon swing. Re-issuing attackEntity every tick would reset the swing so it never fires, so
        // only (re)issue when not already attacking Zulrah with the equipped weapon's style. The policy still decides
        // whether/when to attack and which style; this is just faithful attack mechanics.
        final Action current = player.getActionManager().getAction();
        if (current instanceof PlayerCombat && ((PlayerCombat) current).getTarget() == zulrah) {
            final boolean wantMagic = player.getWeapon() != null && player.getWeapon().getId() == TRIDENT;
            if ((current instanceof MagicCombat) == wantMagic) {
                return;
            }
        }
        PlayerCombat.attackEntity(player, zulrah, null);
    }

    private static boolean consume(final Player player, final int id) {
        final int slot = slotOf(player, id);
        if (slot == -1) {
            return false;
        }
        final Item item = player.getInventory().getItem(slot);
        final Consumable consumable = Consumable.consumables.get(id);
        if (consumable == null || item == null) {
            return false;
        }
        consumable.consume(player, item, slot);
        return true;
    }

    private static int slotOf(final Player player, final int id) {
        for (int slot = 0; slot < 28; slot++) {
            final Item item = player.getInventory().getItem(slot);
            if (item != null && item.getId() == id) {
                return slot;
            }
        }
        return -1;
    }

    private static void setOverhead(final Player player, final Prayer chosen) {
        final Prayer[] protections = {Prayer.PROTECT_FROM_MAGIC, Prayer.PROTECT_FROM_MISSILES, Prayer.PROTECT_FROM_MELEE};
        for (final Prayer prayer : protections) {
            if (prayer != chosen && player.getPrayerManager().isActive(prayer)) {
                player.getPrayerManager().deactivatePrayer(prayer);
            }
        }
        if (chosen != null && !player.getPrayerManager().isActive(chosen)) {
            player.getPrayerManager().activatePrayer(chosen);
        }
    }

    private static String overhead(final Player player) {
        if (player.getPrayerManager().isActive(Prayer.PROTECT_FROM_MAGIC)) {
            return "magic";
        }
        if (player.getPrayerManager().isActive(Prayer.PROTECT_FROM_MISSILES)) {
            return "missiles";
        }
        if (player.getPrayerManager().isActive(Prayer.PROTECT_FROM_MELEE)) {
            return "melee";
        }
        return "none";
    }

    private static String form(final int id) {
        if (id == ZulrahNPC.RANGED) {
            return "range";
        }
        if (id == ZulrahNPC.MELEE) {
            return "melee";
        }
        if (id == ZulrahNPC.MAGIC) {
            return "mage";
        }
        return "unknown";
    }

    /** Arena walkable/blocked grid (template coords) for the bot's instance, so the viewer can draw the real shrine. */
    private static String buildMap(final Bot bot) {
        final ZulrahInstance instance = bot.instance;
        final StringBuilder sb = new StringBuilder(1024);
        sb.append("{\"x0\":").append(ARENA_X0).append(",\"y0\":").append(ARENA_Y0)
                .append(",\"w\":").append(ARENA_X1 - ARENA_X0 + 1).append(",\"h\":").append(ARENA_Y1 - ARENA_Y0 + 1)
                .append(",\"blocked\":[");
        for (int ty = ARENA_Y0; ty <= ARENA_Y1; ty++) {
            if (ty > ARENA_Y0) {
                sb.append(',');
            }
            sb.append('[');
            for (int tx = ARENA_X0; tx <= ARENA_X1; tx++) {
                if (tx > ARENA_X0) {
                    sb.append(',');
                }
                int blocked = 1;
                if (instance != null) {
                    final var loc = instance.getLocation(tx, ty, 0);
                    blocked = World.isFloorFree(0, loc.getX(), loc.getY()) ? 0 : 1;
                }
                sb.append(blocked);
            }
            sb.append(']');
        }
        sb.append("]}");
        return sb.toString();
    }

    /** Full Markov-ish state of one bot as a single JSON line (also the basis for trace/ghost recording). */
    private static String snapshot(final Bot bot) {
        final Player player = bot.player;
        final StringBuilder sb = new StringBuilder(384);
        sb.append('{');
        if (player == null) {
            return sb.append("\"ready\":false}").toString();
        }
        sb.append("\"ready\":true,\"tick\":").append(tickCount);

        final Object lastHitType = player.getTemporaryAttributes().get("rl_last_hit_type");
        final Object lastHitCycle = player.getTemporaryAttributes().get("rl_last_hit_cycle");
        String lastAtk = "none";
        int lastAtkAgo = 99;
        if (lastHitType != null && lastHitCycle != null) {
            final String t = lastHitType.toString();
            lastAtk = "RANGED".equals(t) ? "range" : "MAGIC".equals(t) ? "mage" : "MELEE".equals(t) ? "melee" : "none";
            lastAtkAgo = (int) (com.zenyte.cores.WorldThread.WORLD_CYCLE - ((Number) lastHitCycle).longValue());
        }

        sb.append(",\"player\":{")
                .append("\"x\":").append(player.getX())
                .append(",\"y\":").append(player.getY())
                .append(",\"z\":").append(player.getPlane())
                .append(",\"hp\":").append(player.getHitpoints())
                .append(",\"maxHp\":").append(player.getMaxHitpoints())
                .append(",\"prayer\":").append(player.getPrayerManager().getPrayerPoints())
                .append(",\"overhead\":\"").append(overhead(player)).append('"')
                .append(",\"attack_style\":\"").append(attackStyle(player)).append('"')
                .append(",\"venomed\":").append(player.getToxins().isVenomed())
                .append(",\"poisoned\":").append(player.getToxins().isPoisoned())
                .append(",\"running\":").append(player.isRunning())
                .append(",\"pool\":").append(resourcePool(player))   // conserved resource budget (hp+prayer+supplies)
                .append(",\"last_atk\":\"").append(lastAtk).append('"')        // type of the most recent incoming hit
                .append(",\"last_atk_ago\":").append(lastAtkAgo)               // ticks since it landed (rhythm + flicking)
                .append('}');

        final ZulrahInstance instance = bot.instance;
        final ZulrahNPC zulrah = instance == null ? null : instance.getZulrah();
        int outcome = instance == null ? 0 : instance.getOutcome();
        if (outcome == 0 && zulrah != null && zulrah.isDead()) {
            outcome = 1;
        }
        sb.append(",\"done\":").append(outcome != 0);
        sb.append(",\"outcome\":\"")
                .append(outcome == 1 ? "kill" : outcome == 2 ? "death" : outcome == 3 ? "left" : "ongoing")
                .append('"');

        sb.append(",\"zulrah\":");
        if (zulrah == null) {
            sb.append("{\"present\":false}");
        } else {
            sb.append("{\"present\":true")
                    .append(",\"id\":").append(zulrah.getId())
                    .append(",\"form\":\"").append(form(zulrah.getId())).append('"')
                    .append(",\"x\":").append(zulrah.getX())
                    .append(",\"y\":").append(zulrah.getY())
                    .append(",\"hp\":").append(zulrah.getHitpoints())
                    .append(",\"maxHp\":").append(zulrah.getMaxHitpoints())
                    .append(",\"rotation\":").append(zulrah.getRotation())
                    .append(",\"sequence\":").append(zulrah.getSequence())
                    .append(",\"phase\":").append(zulrah.getPhase())
                    .append('}');
        }

        sb.append(",\"snakelings\":[");
        if (zulrah != null) {
            boolean first = true;
            for (final Object obj : zulrah.getSnakelings()) {
                if (!(obj instanceof com.zenyte.game.world.entity.npc.NPC)) {
                    continue;
                }
                final com.zenyte.game.world.entity.npc.NPC sn = (com.zenyte.game.world.entity.npc.NPC) obj;
                if (!first) {
                    sb.append(',');
                }
                first = false;
                sb.append('[').append(sn.getX()).append(',').append(sn.getY()).append(']');
            }
        }
        sb.append(']');

        sb.append(",\"clouds\":[");
        if (zulrah != null) {
            boolean firstCloud = true;
            for (final var cloud : zulrah.getVenomClouds()) {
                if (cloud == null) {
                    continue;
                }
                if (!firstCloud) {
                    sb.append(',');
                }
                firstCloud = false;
                sb.append('[').append(cloud.getX()).append(',').append(cloud.getY()).append(']');
            }
        }
        sb.append(']');

        sb.append(",\"supplies\":{\"food\":").append(player.getInventory().getAmountOf(SHARK) + player.getInventory().getAmountOf(BREW))
                .append(",\"antivenom\":").append(player.getInventory().getAmountOf(ANTIVENOM))
                .append(",\"prayer\":").append(player.getInventory().getAmountOf(SUPER_RESTORE))
                .append('}');

        // full inventory as [id, amount] for non-empty slots (the viewer maps ids -> labels)
        sb.append(",\"inv\":[");
        boolean firstInv = true;
        for (int slot = 0; slot < 28; slot++) {
            final Item it = player.getInventory().getItem(slot);
            if (it == null) {
                continue;
            }
            if (!firstInv) {
                sb.append(',');
            }
            firstInv = false;
            sb.append('[').append(it.getId()).append(',').append(it.getAmount()).append(']');
        }
        sb.append(']');
        sb.append(",\"weapon\":").append(player.getWeapon() == null ? -1 : player.getWeapon().getId());

        sb.append('}');
        return sb.toString();
    }

    private static String attackStyle(final Player player) {
        final Item weapon = player.getWeapon();
        if (weapon == null) {
            return "none";
        }
        if (weapon.getId() == TRIDENT) {
            return "mage";
        }
        if (weapon.getId() == CROSSBOW) {
            return "range";
        }
        return "none";
    }

    /** Conserved resource pool = HP + prayer + healing left in food + prayer left in restores (dose-aware). */
    private static int resourcePool(final Player player) {
        final var inv = player.getInventory();
        int pool = player.getHitpoints() + player.getPrayerManager().getPrayerPoints();
        pool += inv.getAmountOf(SHARK) * 20;     // shark heals 20
        pool += inv.getAmountOf(BREW) * 16;      // saradomin brew ~16 hp
        final int doses = inv.getAmountOf(3024) * 4 + inv.getAmountOf(3026) * 3
                + inv.getAmountOf(3028) * 2 + inv.getAmountOf(3030);   // super restore dose variants
        pool += doses * 32;                      // ~32 prayer restored per dose
        return pool;
    }

    private ZulrahControl() {
    }
}
