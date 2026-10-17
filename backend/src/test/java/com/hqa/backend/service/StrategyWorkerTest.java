package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;

class StrategyWorkerTest {

    @Test
    void runsOnItsOwnThreadAndNeverOverlapsItself() throws Exception {
        try (StrategyWorker worker = new StrategyWorker("strategy-test")) {
            CountDownLatch started = new CountDownLatch(1);
            CountDownLatch release = new CountDownLatch(1);
            AtomicReference<String> thread = new AtomicReference<>();
            assertThat(worker.submit(() -> {
                thread.set(Thread.currentThread().getName());
                started.countDown();
                try {
                    release.await(5, TimeUnit.SECONDS);
                } catch (InterruptedException ex) {
                    Thread.currentThread().interrupt();
                }
            })).isTrue();
            assertThat(started.await(5, TimeUnit.SECONDS)).isTrue();
            assertThat(thread.get()).isEqualTo("strategy-test");
            assertThat(worker.submit(() -> { })).isFalse(); // the scheduler thread is not blocked, the tick is dropped
            release.countDown();
            for (int i = 0; i < 100 && worker.isRunning(); i++) Thread.sleep(10);
            assertThat(worker.isRunning()).isFalse();
            assertThat(worker.submit(() -> { })).isTrue();
        }
    }
}
