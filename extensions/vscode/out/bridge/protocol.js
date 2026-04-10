"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.makeResponse = makeResponse;
exports.makeError = makeError;
exports.makeRequest = makeRequest;
function makeResponse(id, result) {
    return JSON.stringify({ jsonrpc: '2.0', result, id });
}
function makeError(id, code, message) {
    return JSON.stringify({ jsonrpc: '2.0', error: { code, message }, id });
}
function makeRequest(id, method, params) {
    return JSON.stringify({ jsonrpc: '2.0', method, params, id });
}
//# sourceMappingURL=protocol.js.map