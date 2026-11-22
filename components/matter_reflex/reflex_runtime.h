#pragma once

#include <cstddef>
#include <cstdint>
#include <esp_matter.h>
#include "reflex_engine.h"

// Call on the Matter event loop or with the CHIP stack lock held.
namespace reflex_runtime {
bool lease(uint16_t seconds);
bool replace_image(const uint8_t *data, size_t size);
bool transfer(const char *frame, size_t length);
const char *rules_hash();
bool active();
// Snapshot once per incoming report, before dispatching any of its rows.
enum class Handling { unavailable, direct_only, all };
Handling handling();
uint32_t direct_ready_mask();
uint32_t executions();
uint64_t self_node();
uint64_t fabric_id();
void create_cluster(esp_matter::endpoint_t *ep);
using ResultListener = void (*)(uint32_t rule, uint64_t event, uint16_t status);
void start(void (*on_active_changed)(bool) = nullptr, ResultListener on_result = nullptr);
// Four pages of eight fixed-width hex records, small enough for Matter CharString.
constexpr size_t kJournalPages = 4, kJournalPageChars = 8 * 28;
void journal_page(size_t page, char *output);
uint64_t next_run();
void command_result(uint32_t rule, uint64_t event, uint16_t status, uint64_t run);
} // namespace reflex_runtime
