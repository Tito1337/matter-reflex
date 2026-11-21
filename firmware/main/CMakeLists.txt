set(reflex "../../components/matter_reflex")
idf_component_register(
    SRCS "app_main.cpp" "${reflex}/reflex_engine.cpp" "${reflex}/reflex_http.cpp"
         "${reflex}/reflex_peers.cpp" "${reflex}/reflex_runtime.cpp"
    INCLUDE_DIRS "." "${reflex}"
    PRIV_REQUIRES esp_matter chip openthread esp_http_server esp_timer
                  esp_hw_support esp_driver_usb_serial_jtag mbedtls nvs_flash)
target_compile_definitions(${COMPONENT_LIB} PRIVATE CHIP_HAVE_CONFIG_H)
