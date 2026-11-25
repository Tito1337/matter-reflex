#pragma once

#include "esphome/components/matter_reflex/reflex_runtime.h"
#include "esphome/components/matter_reflex/reflex_peers.h"
#include "esphome/core/log.h"
#ifdef USE_OPENTHREAD_BORDER_ROUTER
#include <app/server/Server.h>
#endif

inline void lab_report_status() {
    esp_matter::lock::ScopedChipStackLock lock(portMAX_DELAY);
#ifdef USE_OPENTHREAD_BORDER_ROUTER
    if (reflex_runtime::self_node()) {
        auto &sessions = chip::Server::GetInstance().GetSecureSessionManager().GetSecureSessions();
        sessions.ForEachSession([](auto *session) {
            if (session->IsCASESession() && session->IsActiveSession())
                ESP_LOGI("lab", "peer_rx=%016llx tick=%llu", static_cast<unsigned long long>(session->GetPeerNodeId()),
                         static_cast<unsigned long long>(session->GetLastPeerActivityTime().count()));
            return chip::Loop::Continue;
        });
    }
#endif
    const auto peers = reflex_peers::readiness();
    ESP_LOGI("lab", "node=%016llx active=%d rules=%s executions=%lu peers=%u/%u",
             static_cast<unsigned long long>(reflex_runtime::self_node()),
             reflex_runtime::active(), reflex_runtime::rules_hash(),
             static_cast<unsigned long>(reflex_runtime::executions()), peers.ready, peers.total);
}
