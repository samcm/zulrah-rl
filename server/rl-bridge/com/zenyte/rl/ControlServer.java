package com.zenyte.rl;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * TCP control socket bridging the RL side (Python gym env) to the game.
 *
 * One connection == one {@link Bot} == one gym env. Newline-delimited text protocol; every command returns exactly one
 * JSON line. Commands run on the world thread (handed off via {@link ZulrahControl}); the connection thread blocks on
 * the reply.
 *
 *   reset            -> spawn/teleport the bot into a fresh Zulrah instance; returns the first observation
 *   step <action>    -> apply the action, advance one game tick; returns the resulting observation
 *   state            -> returns the current observation without stepping
 *   ping             -> {"pong":true}
 */
public final class ControlServer {

    public static final int PORT = 43500;
    private static final AtomicInteger CONNECTIONS = new AtomicInteger();

    public static void start() {
        final Thread t = new Thread(ControlServer::accept, "rl-control-accept");
        t.setDaemon(true);
        t.start();
    }

    private static void accept() {
        try (ServerSocket server = new ServerSocket(PORT)) {
            System.out.println("[RL] control server listening on " + PORT);
            while (true) {
                final Socket socket = server.accept();
                final int id = CONNECTIONS.incrementAndGet();
                final Thread handler = new Thread(() -> handle(socket, id), "rl-conn-" + id);
                handler.setDaemon(true);
                handler.start();
            }
        } catch (final IOException e) {
            System.out.println("[RL] control server stopped: " + e.getMessage());
        }
    }

    private static void handle(final Socket socket, final int id) {
        final Bot bot = new Bot("rlbot" + id);
        System.out.println("[RL] connection " + id + " -> " + bot.name);
        try (BufferedReader in = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8));
             BufferedWriter out = new BufferedWriter(new OutputStreamWriter(socket.getOutputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = in.readLine()) != null) {
                line = line.trim();
                if (line.isEmpty()) {
                    continue;
                }
                String reply;
                try {
                    reply = dispatch(bot, line);
                } catch (final Exception e) {
                    reply = "{\"error\":\"" + escape(String.valueOf(e.getMessage())) + "\"}";
                }
                out.write(reply);
                out.write('\n');
                out.flush();
            }
        } catch (final IOException ignored) {
            // client disconnected
        } finally {
            ZulrahControl.dispose(bot);
            System.out.println("[RL] connection " + id + " closed");
        }
    }

    private static String dispatch(final Bot bot, final String line) throws Exception {
        final String[] parts = line.split("\\s+");
        final char type;
        int arg = 0;
        switch (parts[0]) {
            case "reset":
                type = 'R';
                if (parts.length > 1) {
                    arg = Integer.parseInt(parts[1]);   // curriculum: Zulrah's starting HP (0/absent = full)
                }
                break;
            case "step":
                type = 'S';
                arg = Integer.parseInt(parts[1]);
                break;
            case "state":
                type = 'Q';
                break;
            case "map":
                type = 'M';
                break;
            case "ping":
                return "{\"pong\":true}";
            default:
                return "{\"error\":\"unknown command: " + escape(parts[0]) + "\"}";
        }
        final CompletableFuture<String> future = new CompletableFuture<>();
        ZulrahControl.submit(new ZulrahControl.Job(bot, type, arg, future));
        return future.get(15, TimeUnit.SECONDS);
    }

    static String escape(final String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private ControlServer() {
    }
}
