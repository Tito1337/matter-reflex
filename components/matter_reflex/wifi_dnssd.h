#pragma once

// Adapt only the pinned Matter component's DNS-SD translation unit. Both
// ESPHome's native API and Matter publish through the same mDNS responder.
#ifdef REFLEX_WIFI_DNSSD
#include "esphome/core/defines.h"
#undef USE_OPENTHREAD
#include "esphome/core/application.h"
#include <platform/ESP32/ESP32DnssdImpl.h>

namespace chip::Dnssd {
inline CHIP_ERROR PublishEspHomeService(const DnssdService *service, DnssdPublishCallback callback, void *context) {
    auto local = *service;
    // The SDK also uses this hostname when registering Matter subtypes.
    Platform::CopyString(local.mHostName, esphome::App.get_name().c_str());
    return EspDnssdPublishService(&local, callback, context);
}

inline CHIP_ERROR RemoveEspHomeServices() {
    const char *host = esphome::App.get_name().c_str();
    for (const char *type : {"_matter", "_matterc", "_matterd"}) {
        const char *proto = strcmp(type, "_matter") == 0 ? "_tcp" : "_udp";
        // A null hostname in the SDK's existence check also matches delegated
        // Thread peers, while its removal function only removes local services.
        while (mdns_service_exists(type, proto, host)) {
            if (mdns_service_remove(type, proto) != ESP_OK)
                return CHIP_ERROR_INTERNAL;
        }
    }
    return CHIP_NO_ERROR;
}
}  // namespace chip::Dnssd

#define EspDnssdPublishService PublishEspHomeService
#define EspDnssdRemoveServices RemoveEspHomeServices
#endif
