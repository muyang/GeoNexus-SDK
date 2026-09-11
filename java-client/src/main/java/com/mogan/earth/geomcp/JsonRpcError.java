package com.mogan.earth.geomcp;

import com.fasterxml.jackson.annotation.JsonInclude;
import java.util.Map;

/**
 * JSON-RPC 2.0 错误对象。
 * 错误码：-32700~-32603（协议级），2000~2004（应用级）。
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public class JsonRpcError {

    private int code;
    private String message;
    private Object data;

    public JsonRpcError() {}

    public JsonRpcError(int code, String message, Object data) {
        this.code = code;
        this.message = message;
        this.data = data;
    }

    // ── 便捷判断 ──

    public boolean isContractNotSatisfied() { return code == 2000; }
    public boolean isSkillNotFound()         { return code == 2001; }
    public boolean isGeoCardNotFound()       { return code == 2002; }
    public boolean isExecutionFailed()       { return code == 2003; }
    public boolean isInvalidArgument()       { return code == 2004; }

    @SuppressWarnings("unchecked")
    public Map<String, Object> getDataAsMap() {
        if (data instanceof Map) return (Map<String, Object>) data;
        return null;
    }

    // ── getters / setters ──

    public int getCode() { return code; }
    public void setCode(int code) { this.code = code; }
    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }
    public Object getData() { return data; }
    public void setData(Object data) { this.data = data; }

    @Override
    public String toString() {
        return "GeoMCPError{code=" + code + ", message='" + message + "'}";
    }
}