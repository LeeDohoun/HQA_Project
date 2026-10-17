package com.hqa.backend.service;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * A private single thread for one strategy job. The Spring scheduler thread (shared with the LLM
 * PAPER jobs) only hands the work over, so slow KIS calls of one job cannot starve another, and a
 * job never overlaps itself. Deliberately not a Spring bean: a TaskScheduler/Executor bean would
 * replace Spring Boot's default ones used by the existing schedulers.
 */
final class StrategyWorker implements AutoCloseable {
    private final ExecutorService pool;
    private final AtomicBoolean running = new AtomicBoolean();

    StrategyWorker(String name) {
        this.pool = Executors.newSingleThreadExecutor(task -> {
            Thread thread = new Thread(task, name);
            thread.setDaemon(true);
            return thread;
        });
    }

    /** @return false when the previous run is still going (or the worker is closed) */
    boolean submit(Runnable task) {
        if (!running.compareAndSet(false, true)) return false;
        try {
            pool.execute(() -> {
                try {
                    task.run();
                } finally {
                    running.set(false);
                }
            });
            return true;
        } catch (RejectedExecutionException ex) {
            running.set(false);
            return false;
        }
    }

    boolean isRunning() {
        return running.get();
    }

    @Override
    public void close() {
        pool.shutdownNow();
    }
}
