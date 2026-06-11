package com.zenyte.rl;

import com.zenyte.game.content.boss.zulrah.ZulrahInstance;
import com.zenyte.game.world.entity.player.Player;

/**
 * One RL-controlled headless bot: a {@link Player} plus its current {@link ZulrahInstance}.
 * One {@link Bot} per control-socket connection (= one gym env).
 */
public final class Bot {

    final String name;
    volatile Player player;
    volatile ZulrahInstance instance;

    Bot(final String name) {
        this.name = name;
    }

    public String getName() {
        return name;
    }

    public Player getPlayer() {
        return player;
    }

    public ZulrahInstance getInstance() {
        return instance;
    }
}
