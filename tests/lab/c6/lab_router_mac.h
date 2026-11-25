#pragma once
#include <esp_mac.h>

inline void lab_router_mac() {
    // Two instances of the same virtual chip can share a factory station MAC.
    const uint8_t mac[] = {0x02, 0x52, 0x46, 0x00, 0x00, 0x03};
    ESP_ERROR_CHECK(esp_base_mac_addr_set(mac));
}
