idf_component_register(SRC_DIRS "." INCLUDE_DIRS "."
    PRIV_REQUIRES esp_matter chip openthread esp_http_server esp_timer
                  esp_hw_support esp_driver_usb_serial_jtag mbedtls nvs_flash)
target_compile_definitions(${COMPONENT_LIB} PRIVATE CHIP_HAVE_CONFIG_H)
