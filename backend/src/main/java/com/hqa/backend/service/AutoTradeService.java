package com.hqa.backend.service;

import com.hqa.backend.entity.User;
import com.hqa.backend.repository.UserRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 사용자별 자동매매 ON/OFF 상태를 영구 저장한다 (users.auto_trade_enabled).
 */
@Service
public class AutoTradeService {

    private final UserRepository userRepository;

    public AutoTradeService(UserRepository userRepository) {
        this.userRepository = userRepository;
    }

    @Transactional(readOnly = true)
    public boolean isEnabled(User user) {
        return user != null && user.isAutoTradeEnabled();
    }

    @Transactional
    public boolean setEnabled(User user, boolean value) {
        user.setAutoTradeEnabled(value);
        userRepository.save(user);
        return value;
    }
}
