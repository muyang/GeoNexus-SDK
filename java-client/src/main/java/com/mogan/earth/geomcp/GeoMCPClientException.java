package com.mogan.earth.geomcp;

/**
 * GeoMCP 客户端异常 — 统一封装所有调用错误。
 */
public class GeoMCPClientException extends RuntimeException {

    private final int code;

    public GeoMCPClientException(String message) {
        super(message);
        this.code = -1;
    }

    public GeoMCPClientException(int code, String message) {
        super(message);
        this.code = code;
    }

    public GeoMCPClientException(int code, String message, Throwable cause) {
        super(message, cause);
        this.code = code;
    }

    public int getCode() { return code; }

    public boolean isContractNotSatisfied() { return code == 2000; }
    public boolean isSkillNotFound()         { return code == 2001; }
    public boolean isGeoCardNotFound()       { return code == 2002; }
    public boolean isExecutionFailed()       { return code == 2003; }
    public boolean isInvalidArgument()       { return code == 2004; }

    @Override
    public String toString() {
        return "GeoMCPClientException{code=" + code + ", message='" + getMessage() + "'}";
    }
}