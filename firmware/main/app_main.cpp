#include "reflex_runtime.h"

#include <app/server/Server.h>
#include <driver/usb_serial_jtag.h>
#include <esp_openthread_types.h>
#include <lib/support/ThreadOperationalDataset.h>
#include <mbedtls/platform_util.h>
#include <nvs_flash.h>
#include <platform/CHIPDeviceLayer.h>
#include <platform/ESP32/OpenthreadLauncher.h>
#include <platform/ThreadStackManager.h>
#include <cstdio>
#include <cstring>

namespace {
using namespace chip;
using namespace chip::DeviceLayer;
using namespace esp_matter;
bool pairing_window() {
    if (Server::GetInstance().GetFabricTable().FabricCount() != 0 || !ThreadStackMgr().IsThreadProvisioned()) return false;
    auto &window = Server::GetInstance().GetCommissioningWindowManager();
    return window.IsCommissioningWindowOpen() ||
           window.OpenBasicCommissioningWindow(System::Clock::Seconds16(900), CommissioningWindowAdvertisement::kDnssdOnly) == CHIP_NO_ERROR;
}
int hex_digit(char c) {
    if (c >= '0' && c <= '9') return c-'0';
    if (c >= 'a' && c <= 'f') return c-'a'+10;
    if (c >= 'A' && c <= 'F') return c-'A'+10;
    return -1;
}
bool join(const char *hex) {
    // Only USB provisioning of an uncommissioned device may change its mesh.
    if (Server::GetInstance().GetFabricTable().FabricCount() != 0) return false;
    size_t n = strlen(hex);
    uint8_t buf[Thread::kSizeOperationalDataset]{};
    if (n == 0 || n%2 || n/2 > sizeof(buf)) return false;
    bool valid = true;
    for (size_t i = 0; i < n/2; ++i) {
        int a = hex_digit(hex[2*i]), b = hex_digit(hex[2*i+1]);
        if (a < 0 || b < 0) { valid = false; break; }
        buf[i] = (a << 4) | b;
    }
    Thread::OperationalDatasetView dataset;
    ByteSpan bytes(buf, n/2);
    auto &thread = ThreadStackMgr();
    bool ok = valid && dataset.Init(bytes) == CHIP_NO_ERROR && dataset.IsCommissioned();
    if (ok) {
        ok = thread.SetThreadEnabled(false) == CHIP_NO_ERROR &&
             thread.SetThreadProvision(bytes) == CHIP_NO_ERROR &&
             thread.SetThreadEnabled(true) == CHIP_NO_ERROR && pairing_window();
    }
    mbedtls_platform_zeroize(buf, sizeof(buf));
    return ok;
}

void command(char *line) {
    lock::ScopedChipStackLock lock(portMAX_DELAY);
    bool ok = false;
    if (!strncmp(line, "dataset ", 8)) ok = join(line+8);
    else if (!strcmp(line, "pair")) ok = pairing_window();
    else if (!strcmp(line, "status")) {
        printf("thread=%s fabrics=%u node=%016llx active=%d rules=%s executions=%lu\n",
               ThreadStackMgr().IsThreadAttached() ? "attached" : "detached",
               unsigned(Server::GetInstance().GetFabricTable().FabricCount()),
               static_cast<unsigned long long>(reflex_runtime::self_node()),
               reflex_runtime::active(), reflex_runtime::rules_hash(),
               static_cast<unsigned long>(reflex_runtime::executions()));
        return;
    }
    puts(ok ? "REFLEX OK" : "REFLEX ERROR");
}

void usb_console(void *) {
    if (!usb_serial_jtag_is_driver_installed()) {
        usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
        ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&cfg));
    }
    // Read raw bytes without shell history, echo or printing invalid input.
    char line[2*Thread::kSizeOperationalDataset + 16]{};
    size_t used = 0;
    bool overflow = false;
    for (;;) {
        uint8_t c;
        if (usb_serial_jtag_read_bytes(&c, 1, portMAX_DELAY) != 1) continue;
        if (c == '\r' || c == '\n') {
            if (!overflow && used) { line[used] = 0; command(line); }
            else if (overflow) puts("REFLEX ERROR");
            mbedtls_platform_zeroize(line, sizeof(line));
            used = 0;
            overflow = false;
        } else if (c >= 32 && c <= 126 && used < sizeof(line)-1 && !overflow) line[used++] = c;
        else overflow = true;
    }
}
esp_err_t identify(identification::callback_type_t, uint16_t, uint8_t, uint8_t, void *) { return ESP_OK; }
void device_event(const ChipDeviceEvent *, intptr_t) {}
} // namespace

extern "C" void app_main() {
    ESP_ERROR_CHECK(nvs_flash_init());
    esp_matter::node::config_t cfg;
    auto *node = esp_matter::node::create(&cfg, nullptr, identify);
    ESP_ERROR_CHECK(node ? ESP_OK : ESP_ERR_NO_MEM);
    esp_matter::endpoint::on_off_light_switch::config_t sw;
    auto *ep = esp_matter::endpoint::on_off_light_switch::create(node, &sw, esp_matter::ENDPOINT_FLAG_NONE, nullptr);
    ESP_ERROR_CHECK(ep && esp_matter::endpoint::get_id(ep) == 1 ? ESP_OK : ESP_FAIL);
    reflex_runtime::create_cluster(ep);
    // EP2 is a logical output. Only the legacy timed command starts a CHIP countdown.
    esp_matter::endpoint::on_off_plug_in_unit::config_t output;
    auto *inhibit = esp_matter::endpoint::on_off_plug_in_unit::create(node, &output, esp_matter::ENDPOINT_FLAG_NONE, nullptr);
    ESP_ERROR_CHECK(inhibit && esp_matter::endpoint::get_id(inhibit) == 2 ? ESP_OK : ESP_FAIL);

    esp_openthread_platform_config_t platform{
        .radio_config = {.radio_mode = RADIO_MODE_NATIVE},
        .host_config = {.host_connection_mode = HOST_CONNECTION_MODE_NONE},
        .port_config = {.storage_partition_name = "nvs", .netif_queue_size = 10, .task_queue_size = 10}
    };
    set_openthread_platform_config(&platform);
    ESP_ERROR_CHECK(esp_matter::start(device_event));
    {
        esp_matter::lock::ScopedChipStackLock lock(portMAX_DELAY);
        reflex_runtime::start();
        auto &thread = chip::DeviceLayer::ThreadStackMgr();
        if (thread.IsThreadProvisioned() && !thread.IsThreadEnabled()) {
            ESP_ERROR_CHECK(thread.SetThreadEnabled(true) == CHIP_NO_ERROR ? ESP_OK : ESP_FAIL);
        }
    }
    ESP_ERROR_CHECK(xTaskCreate(usb_console, "reflex_usb", 6144, nullptr, 4, nullptr) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
}
