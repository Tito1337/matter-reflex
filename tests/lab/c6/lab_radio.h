#pragma once

#include "esphome/components/openthread/openthread.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"
#include <openthread/link.h>
#include <openthread/thread.h>

inline void lab_report_radio() {
    auto lock = esphome::openthread::InstanceLock::try_acquire(0);
    if (!lock)
        return;
    auto *instance = lock.get_instance();
    if (!instance)
        return;
    char address[17];
    auto role = otThreadGetDeviceRole(instance);
    static int previous_role = -1;
    if (previous_role != role) {
        ESP_LOGI("lab_radio", "role=%s rx_on_when_idle=%d channel=%u", otThreadDeviceRoleToString(role),
                 otThreadGetLinkMode(instance).mRxOnWhenIdle, otLinkGetChannel(instance));
        previous_role = role;
    }
    if (role == OT_DEVICE_ROLE_ROUTER || role == OT_DEVICE_ROLE_LEADER) {
        ESP_LOGI("lab_radio", "router=%s", esphome::format_hex_to(address, otLinkGetExtendedAddress(instance)->m8, 8));
    } else if (role == OT_DEVICE_ROLE_CHILD) {
        otRouterInfo parent{};
        if (otThreadGetParentInfo(instance, &parent) == OT_ERROR_NONE)
            ESP_LOGI("lab_radio", "parent=%s sleepy=%d", esphome::format_hex_to(address, parent.mExtAddress.m8, 8),
                     !otThreadGetLinkMode(instance).mRxOnWhenIdle);
    }
}
