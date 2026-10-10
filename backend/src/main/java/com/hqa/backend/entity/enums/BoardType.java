package com.hqa.backend.entity.enums;

public enum BoardType {
    /** 자유게시판 */
    FREE,
    /** 종목토론방. stock_code가 반드시 있다. */
    STOCK,
    /** 관리자 문의. 작성자와 관리자만 볼 수 있다. */
    INQUIRY
}
