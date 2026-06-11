package com.zenyte.rl;

import io.netty.channel.embedded.EmbeddedChannel;

import java.net.InetSocketAddress;
import java.net.SocketAddress;

/**
 * A network channel for a headless "bot" {@link com.zenyte.game.world.entity.player.Player} that has no real client.
 *
 * It is a Netty {@link EmbeddedChannel} (open + active by default, buffers all outbound packets in memory) with one
 * tweak: {@link #remoteAddress()} returns a real {@link InetSocketAddress} so the login/onLogin code paths that call
 * {@code channel.remoteAddress()} (and {@code instanceof InetSocketAddress}) do not NPE or misbehave. Outbound packets
 * must be drained each tick (see ZulrahControl) to avoid unbounded growth.
 */
public final class BotChannel extends EmbeddedChannel {

    private static final InetSocketAddress LOOPBACK = new InetSocketAddress("127.0.0.1", 0);

    @Override
    public SocketAddress remoteAddress() {
        return LOOPBACK;
    }

    @Override
    public SocketAddress localAddress() {
        return LOOPBACK;
    }
}
