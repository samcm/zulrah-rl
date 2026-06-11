package com.zenyte.rl;

import com.zenyte.game.content.boss.zulrah.ZulrahInstance;
import com.zenyte.game.content.boss.zulrah.ZulrahNPC;
import com.zenyte.game.packet.out.FreeCam;
import com.zenyte.game.tasks.TickTask;
import com.zenyte.game.tasks.WorldTask;
import com.zenyte.game.tasks.WorldTasksManager;
import com.zenyte.game.world.entity.Location;
import com.zenyte.game.world.entity.player.Player;

/**
 * Drops a real (client-connected) viewer player into an RL bot's live Zulrah instance as a hidden, locked, free-cam
 * spectator so the fight - bot, Zulrah, snakelings, venom clouds and projectiles - renders in a normal 3D client.
 *
 * The bot fights in a dynamically-allocated {@link ZulrahInstance}. {@code GlobalAreaManager} maps dynamic areas by
 * chunk and re-resolves a player's area from its location every tick, so teleporting the viewer into the bot's
 * allocated chunk makes the area system add the viewer to the same {@link ZulrahInstance} ({@code enter()} only spawns
 * Zulrah for the bot itself, so a spectator is inert). The viewer is then hidden + locked + frozen so it neither
 * appears in the scene nor interferes with the fight, and put into free-cam pointed at Zulrah.
 *
 * Modelled on {@code TournamentSpectatingInterface} (hidden / locked / frozen / free-cam) and the {@code ZulrahInstance}
 * launch cinematic (camera framing of the shrine).
 */
public final class SpectateZulrah {

    // Zulrah arena template coords (the static map the instance is copied from).
    private static final Location SHRINE_VIEW = new Location(2256, 3064, 0); // SW corner: good third-person framing
    private static final Location ZULRAH_TILE = new Location(2266, 3073, 0);  // where Zulrah spawns
    private static final Location ARENA_CENTRE = new Location(2266, 3071, 0); // centre: anchor the loaded scene here

    private SpectateZulrah() {
    }

    /**
     * Begins spectating the bot at {@code index} in {@link ZulrahControl}'s bot list. Returns a human-readable status
     * string (also suitable to message the player).
     */
    public static String start(final Player viewer, final int index) {
        final int count = ZulrahControl.botCount();
        if (count == 0) {
            return "No RL bots are currently active.";
        }
        final Bot bot = ZulrahControl.getBot(index);
        if (bot == null) {
            return "No bot at index " + index + " (" + count + " active; valid 0.." + (count - 1) + ").";
        }
        final ZulrahInstance instance = bot.getInstance();
        if (instance == null || instance.getArea() == null) {
            return "Bot '" + bot.getName() + "' has no active Zulrah instance yet (reset in progress?). Try again shortly.";
        }

        // Anchor the viewer at the arena centre so the loaded scene is just this one instance. Standing at the SW
        // corner with a wide view swept in the tightly-packed neighbouring bot instances (the "50 Zulrahs" pile-up).
        final Location dst = instance.getLocation(ARENA_CENTRE);

        // Hide + lock + freeze so the viewer is invisible and cannot move, attack or be attacked.
        viewer.setHidden(true);
        viewer.stop(Player.StopType.INTERFACES, Player.StopType.ROUTE_EVENT, Player.StopType.WALK,
                Player.StopType.ACTIONS, Player.StopType.ANIMATIONS, Player.StopType.WORLD_MAP);
        viewer.lock(Integer.MAX_VALUE);
        viewer.freeze(Integer.MAX_VALUE);
        viewer.getTemporaryAttributes().put("rl_spectating", Boolean.TRUE);

        // Teleport into the instance; GlobalAreaManager.update() will add us to the ZulrahInstance area next tick.
        viewer.setLocation(dst);
        viewer.setViewDistance(13);   // ~one arena from the centre; excludes the adjacent instances' Zulrahs

        // After the map regions around the new location have loaded, point the camera at the shrine / Zulrah, free-roam.
        WorldTasksManager.schedule(new TickTask() {
            @Override
            public void run() {
                switch (ticks++) {
                    case 1:
                        viewer.loadMapRegions();
                        return;
                    case 2:
                        viewer.send(new FreeCam(true));
                        aimCamera(viewer, instance);
                        stop();
                }
            }
        }, 0, 0);

        return "Spectating bot '" + bot.getName() + "' (index " + index + "). Type ::unspectate to stop.";
    }

    /** Points the viewer's camera at Zulrah (falling back to the shrine spawn tile) from the SW viewing corner. */
    private static void aimCamera(final Player viewer, final ZulrahInstance instance) {
        final Location regionTile = viewer.getLastLoadedMapRegionTile();
        if (regionTile == null) {
            return;
        }
        final Location camPos = instance.getLocation(SHRINE_VIEW);
        final ZulrahNPC zulrah = instance.getZulrah();
        final Location lookAt = zulrah != null
                ? new Location(zulrah.getX(), zulrah.getY(), 0)
                : instance.getLocation(ZULRAH_TILE);
        viewer.getPacketDispatcher().sendCameraPosition(camPos.getLocalX(regionTile), camPos.getLocalY(regionTile), 1100, -1, -1);
        viewer.getPacketDispatcher().sendCameraLook(lookAt.getLocalX(regionTile), lookAt.getLocalY(regionTile), 400, -1, -1);
    }

    /**
     * Re-aims the camera at Zulrah's current tile - call periodically while spectating so the camera tracks the boss as
     * it moves around the arena. (Optional; the static SW framing already keeps the whole arena in view.)
     */
    public static void retrack(final Player viewer) {
        if (!Boolean.TRUE.equals(viewer.getTemporaryAttributes().get("rl_spectating"))) {
            return;
        }
        final Object area = viewer.getArea();
        if (area instanceof ZulrahInstance) {
            aimCamera(viewer, (ZulrahInstance) area);
        }
    }

    /** Restores a spectating viewer to a normal, controllable player and teleports it out of the instance. */
    public static String stop(final Player viewer) {
        if (!Boolean.TRUE.equals(viewer.getTemporaryAttributes().remove("rl_spectating"))) {
            return "You are not spectating.";
        }
        viewer.send(new FreeCam(false));
        viewer.getPacketDispatcher().resetCamera();
        viewer.setHidden(false);
        viewer.resetFreeze();
        viewer.unlock();
        viewer.resetViewDistance();
        // Home teleport tile (Zul-Andra pier area is fine; use a safe known town tile).
        final Location home = new Location(2213, 3056, 0);
        WorldTasksManager.schedule(new WorldTask() {
            @Override
            public void run() {
                viewer.setLocation(home);
                viewer.loadMapRegions();
                stop();
            }
        }, 0, 0);
        return "Stopped spectating.";
    }
}
