#include "reflex_http.h"
#include "reflex_runtime.h"
#include <array>
#include <algorithm>
#include <cinttypes>
#include <cstdio>
#include <cstring>
#include <string>

#include <esp_http_server.h>
#include <esp_random.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <mbedtls/md.h>
#include <mbedtls/platform_util.h>
#include <nvs.h>
#include <platform/CHIPDeviceLayer.h>

namespace {
using Key = std::array<uint8_t, 32>;
struct Challenge { char nonce[33]{}; int64_t expires{}; };
Challenge challenges[8];
struct Credential { Key secret; uint64_t fabric; uint64_t node; };
Credential credential{};
Key &key = credential.secret;

bool save(const Credential &next) {
    nvs_handle_t handle;
    if (nvs_open("reflex_net", NVS_READWRITE, &handle) != ESP_OK) return false;
    bool ok = nvs_set_blob(handle, "credential", &next, sizeof(next)) == ESP_OK && nvs_commit(handle) == ESP_OK;
    nvs_close(handle);
    return ok;
}
bool configured = false;
SemaphoreHandle_t mutex;
httpd_handle_t server;

void hex(const uint8_t *data, size_t count, char *out) {
    constexpr char digits[] = "0123456789abcdef";
    for (size_t i=0; i<count; ++i) {
        out[2*i] = digits[data[i] >> 4]; out[2*i+1] = digits[data[i] & 15];
    }
    out[2*count] = 0;
}
int nibble(char c) {
    if (c >= '0' && c <= '9') return c-'0';
    if (c >= 'a' && c <= 'f') return c-'a'+10;
    if (c >= 'A' && c <= 'F') return c-'A'+10;
    return -1;
}
bool mac(const Key &secret, const char *prefix, const char *nonce, const char *path,
         const uint8_t *body, size_t size, char *out) {
    const auto *md = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    uint8_t sum[32];
    char hash[65], message[160];
    if (!md || mbedtls_md(md, body, size, sum)) return false;
    hex(sum, sizeof(sum), hash);
    const int n = snprintf(message, sizeof(message), "%s\n%s\n%s\n%s%s", prefix, nonce,
                           path, strcmp(prefix, "RES") == 0 ? "200\n" : "", hash);
    if (n < 0 || size_t(n) >= sizeof(message) ||
        mbedtls_md_hmac(md, secret.data(), secret.size(),
                        reinterpret_cast<const uint8_t *>(message), n, sum)) return false;
    hex(sum, sizeof(sum), out);
    return true;
}
bool equal_mac(const char *a, const char *b) {
    uint8_t diff = 0;
    for (size_t i=0; i<64; ++i) diff |= uint8_t(a[i] ^ b[i]);
    return diff == 0;
}
esp_err_t fail(httpd_req_t *req, const char *status) {
    httpd_resp_set_status(req, status);
    httpd_resp_send(req, "", 0);
    return ESP_FAIL;  // close any connection with an unread request body
}
esp_err_t challenge(httpd_req_t *req) {
    uint8_t bytes[16];
    esp_fill_random(bytes, sizeof(bytes));
    char nonce[33];
    hex(bytes, sizeof(bytes), nonce);
    xSemaphoreTake(mutex, portMAX_DELAY);
    const auto now = esp_timer_get_time();
    bool issued = false;
    for (auto &slot : challenges) {
        if (configured && slot.expires <= now) {
            memcpy(slot.nonce, nonce, sizeof(nonce));
            slot.expires = now + 10000000;
            issued = true;
            break;
        }
    }
    xSemaphoreGive(mutex);
    if (!issued) return fail(req, "503 Service Unavailable");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_send(req, nonce, 32);
}
esp_err_t request(httpd_req_t *req) {
    const char *path = static_cast<const char *>(req->user_ctx);
    const bool status = strcmp(path, "/v1/status") == 0;
    const bool activity = strcmp(path, "/v1/activity") == 0;
    const bool read = status || activity;
    const size_t limit = read ? 0 : reflex::kMaxImage;
    const size_t size = req->content_len;
    if (size > limit || (!read && !size)) return fail(req, "400 Bad Request");
    char nonce[33], signature[65];
    if (httpd_req_get_hdr_value_len(req, "X-Reflex-Nonce") != 32 ||
        httpd_req_get_hdr_value_len(req, "X-Reflex-Mac") != 64 ||
        httpd_req_get_hdr_value_str(req, "X-Reflex-Nonce", nonce, sizeof(nonce)) != ESP_OK ||
        httpd_req_get_hdr_value_str(req, "X-Reflex-Mac", signature, sizeof(signature)) != ESP_OK)
        return fail(req, "403 Forbidden");
    std::array<uint8_t, reflex::kMaxImage> body{};
    size_t done = 0;
    while (done < size) {
        const int n = httpd_req_recv(req, reinterpret_cast<char *>(body.data()+done), size-done);
        if (n <= 0) return ESP_FAIL;
        done += n;
    }
    char expected[65], response[200 + reflex_runtime::kJournalPages * reflex_runtime::kJournalPageChars]{};
    bool authorized = false, applied = false, signed_reply = false;
    xSemaphoreTake(mutex, portMAX_DELAY);
    if (configured && mac(key, "REQ", nonce, path, body.data(), size, expected) &&
        equal_mac(expected, signature)) {
        for (auto &slot : challenges) {
            if (slot.expires > esp_timer_get_time() && strcmp(nonce, slot.nonce) == 0) {
                slot.expires = 0;
                authorized = true;
                break;
            }
        }
    }
    Key signing_key = key;
    xSemaphoreGive(mutex);
    if (authorized) {
        chip::DeviceLayer::PlatformMgr().LockChipStack();
        const auto node = reflex_runtime::self_node();
        const auto fabric = reflex_runtime::fabric_id();
        xSemaphoreTake(mutex, portMAX_DELAY);
        bool same_key = key == signing_key;
        if (same_key && status && node && !credential.node) {
            Credential bound{signing_key, fabric, node};
            if (save(bound)) credential = bound;
            mbedtls_platform_zeroize(&bound, sizeof(bound));
        }
        authorized = same_key && node && credential.node == node && credential.fabric == fabric;
        xSemaphoreGive(mutex);
        if (!authorized) {
            applied = false;
        } else if (activity) {
            size_t pos = snprintf(response, sizeof(response), "{\"hash\":\"%s\",\"journal\":\"", reflex_runtime::rules_hash());
            for (size_t page = 0; page < reflex_runtime::kJournalPages; ++page) {
                reflex_runtime::journal_page(page, response + pos);
                pos += strlen(response + pos);
            }
            strcpy(response + pos, "\"}");
            applied = true;
        } else if (status) {
            applied = node != 0;
            snprintf(response, sizeof(response),
                     "{\"node_id\":\"%016" PRIx64 "\",\"hash\":\"%s\",\"active\":%s,\"executions\":%" PRIu32 "}",
                     node, reflex_runtime::rules_hash(), reflex_runtime::active() ? "true" : "false",
                     reflex_runtime::executions());
        } else {
            applied = reflex_runtime::replace_image(body.data(), size);
        }
        chip::DeviceLayer::PlatformMgr().UnlockChipStack();
        signed_reply = applied && mac(signing_key, "RES", nonce, path,
                             reinterpret_cast<const uint8_t *>(response), strlen(response), expected);
    }
    mbedtls_platform_zeroize(signing_key.data(), signing_key.size());
    if (!authorized) return fail(req, "403 Forbidden");
    if (!signed_reply) return fail(req, "409 Conflict");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_set_hdr(req, "X-Reflex-Mac", expected);
    return httpd_resp_sendstr(req, response);
}
}

static bool configure_key(const char *value, bool preserve_binding) {
    if (!mutex || !value || strlen(value) != 64) return false;
    Key next{};
    for (size_t i=0; i<32; ++i) {
        int a = nibble(value[2*i]), b = nibble(value[2*i+1]);
        if (a < 0 || b < 0) { mbedtls_platform_zeroize(next.data(), next.size()); return false; }
        next[i] = (a << 4) | b;
    }
    xSemaphoreTake(mutex, portMAX_DELAY);
    if (preserve_binding && configured && next == key) {
        xSemaphoreGive(mutex);
        mbedtls_platform_zeroize(next.data(), next.size());
        return true;
    }
    Credential replacement{next, 0, 0};
    bool ok = save(replacement);
    if (ok) {
        credential = replacement; configured = true;
        for (auto &slot : challenges) slot.expires = 0;
    }
    xSemaphoreGive(mutex);
    mbedtls_platform_zeroize(&replacement, sizeof(replacement));
    mbedtls_platform_zeroize(next.data(), next.size());
    return ok;
}
bool reflex_http_set_key(const char *value) {
    return configure_key(value, false);
}

esp_err_t reflex_http_start(const char *boot_key) {
    if (server) return ESP_OK;
    if (!mutex) mutex = xSemaphoreCreateMutex();
    if (!mutex) return ESP_ERR_NO_MEM;
    nvs_handle_t nvs;
    if (nvs_open("reflex_net", NVS_READONLY, &nvs) == ESP_OK) {
        size_t size = sizeof(credential);
        configured = nvs_get_blob(nvs, "credential", &credential, &size) == ESP_OK && size == sizeof(credential);
        nvs_close(nvs);
    }
    if (boot_key && !configure_key(boot_key, true)) return ESP_FAIL;
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.server_port = 5581; cfg.stack_size = 12288; cfg.recv_wait_timeout = 2;
    cfg.send_wait_timeout = 2; cfg.lru_purge_enable = true;
    esp_err_t err = httpd_start(&server, &cfg);
    if (err != ESP_OK) return err;
    for (const char *path : {"/v1/challenge", "/v1/status", "/v1/rules", "/v1/activity"}) {
        httpd_uri_t handler{};
        handler.uri = path;
        const bool get = strcmp(path, "/v1/challenge") == 0;
        handler.method = get ? HTTP_GET : HTTP_POST;
        handler.handler = get ? challenge : request;
        handler.user_ctx = const_cast<char *>(path);
        err = httpd_register_uri_handler(server, &handler);
        if (err != ESP_OK) { httpd_stop(server); server = nullptr; return err; }
    }
    return ESP_OK;
}
