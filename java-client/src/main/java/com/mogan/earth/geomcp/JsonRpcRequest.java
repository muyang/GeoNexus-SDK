package com.mogan.earth.geomcp;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;
import java.util.Map;

/**
 * 标准 JSON-RPC 2.0 请求信封。
 * 由 GeoMCPClient 内部构造，业务代码不直接使用。
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public class JsonRpcRequest {

    @JsonProperty("jsonrpc")
    private final String jsonrpc = "2.0";

    @JsonProperty("id")
    private String id;

    @JsonProperty("method")
    private String method;

    @JsonProperty("params")
    private Object params;

    public JsonRpcRequest() {}

    public JsonRpcRequest(String id, String method, Object params) {
        this.id = id;
        this.method = method;
        this.params = params;
    }

    public String getJsonrpc() { return jsonrpc; }
    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getMethod() { return method; }
    public void setMethod(String method) { this.method = method; }
    public Object getParams() { return params; }
    public void setParams(Object params) { this.params = params; }
}