#include <stdio.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "mqtt_client.h"
#include "nvs_flash.h"

#define WIFI_CONNECTED_BIT BIT0
#define AHT20_ADDRESS 0x38
#define FAN_COMMAND_TOPIC "smarthome/device/fan-1/set"
#define FAN_STATE_TOPIC "smarthome/device/fan-1/state"
#define ENVIRONMENT_TOPIC "smarthome/sensor/environment"
#define CONTROLLER_STATUS_TOPIC "smarthome/controller/esp32-s3/status"

static const char *TAG = "smarthome";
static EventGroupHandle_t wifi_event_group;
static esp_mqtt_client_handle_t mqtt_client;
static i2c_master_dev_handle_t aht20_device;

static void publish_fan_state(bool enabled)
{
    if (mqtt_client == NULL) {
        return;
    }
    esp_mqtt_client_publish(
        mqtt_client,
        FAN_STATE_TOPIC,
        enabled ? "on" : "off",
        0,
        1,
        true
    );
}

static void set_fan(bool enabled)
{
    gpio_set_level(CONFIG_SMARTHOME_FAN_GPIO, enabled ? 1 : 0);
    publish_fan_state(enabled);
    ESP_LOGI(TAG, "风扇状态：%s", enabled ? "开启" : "关闭");
}

static void wifi_event_handler(
    void *arg,
    esp_event_base_t event_base,
    int32_t event_id,
    void *event_data
)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(wifi_event_group, WIFI_CONNECTED_BIT);
        esp_wifi_connect();
        ESP_LOGW(TAG, "Wi-Fi 断开，正在重连");
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(wifi_event_group, WIFI_CONNECTED_BIT);
        ESP_LOGI(TAG, "Wi-Fi 已连接");
    }
}

static void wifi_init(void)
{
    wifi_event_group = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&config));
    ESP_ERROR_CHECK(
        esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, NULL)
    );
    ESP_ERROR_CHECK(
        esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler, NULL)
    );

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = CONFIG_SMARTHOME_WIFI_SSID,
            .password = CONFIG_SMARTHOME_WIFI_PASSWORD,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
}

static void mqtt_event_handler(
    void *handler_args,
    esp_event_base_t base,
    int32_t event_id,
    void *event_data
)
{
    esp_mqtt_event_handle_t event = event_data;
    if (event_id == MQTT_EVENT_CONNECTED) {
        ESP_LOGI(TAG, "MQTT 已连接");
        esp_mqtt_client_subscribe(mqtt_client, FAN_COMMAND_TOPIC, 1);
        esp_mqtt_client_publish(
            mqtt_client,
            CONTROLLER_STATUS_TOPIC,
            "online",
            0,
            1,
            true
        );
        publish_fan_state(gpio_get_level(CONFIG_SMARTHOME_FAN_GPIO) == 1);
    } else if (event_id == MQTT_EVENT_DATA) {
        if (
            event->topic_len == strlen(FAN_COMMAND_TOPIC)
            && strncmp(event->topic, FAN_COMMAND_TOPIC, event->topic_len) == 0
        ) {
            bool turn_on = event->data_len == 2
                && strncmp(event->data, "on", event->data_len) == 0;
            bool turn_off = event->data_len == 3
                && strncmp(event->data, "off", event->data_len) == 0;
            if (turn_on || turn_off) {
                set_fan(turn_on);
            }
        }
    } else if (event_id == MQTT_EVENT_DISCONNECTED) {
        ESP_LOGW(TAG, "MQTT 已断开");
    }
}

static void mqtt_init(void)
{
    const esp_mqtt_client_config_t config = {
        .broker.address.uri = CONFIG_SMARTHOME_MQTT_URI,
        .credentials.client_id = "smarthome-esp32-s3",
        .session.last_will = {
            .topic = CONTROLLER_STATUS_TOPIC,
            .msg = "offline",
            .msg_len = 7,
            .qos = 1,
            .retain = true,
        },
    };
    mqtt_client = esp_mqtt_client_init(&config);
    ESP_ERROR_CHECK(
        esp_mqtt_client_register_event(
            mqtt_client,
            ESP_EVENT_ANY_ID,
            mqtt_event_handler,
            NULL
        )
    );
    ESP_ERROR_CHECK(esp_mqtt_client_start(mqtt_client));
}

static void aht20_init(void)
{
    i2c_master_bus_config_t bus_config = {
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .i2c_port = I2C_NUM_0,
        .scl_io_num = CONFIG_SMARTHOME_I2C_SCL_GPIO,
        .sda_io_num = CONFIG_SMARTHOME_I2C_SDA_GPIO,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    i2c_master_bus_handle_t bus_handle;
    ESP_ERROR_CHECK(i2c_new_master_bus(&bus_config, &bus_handle));

    i2c_device_config_t device_config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = AHT20_ADDRESS,
        .scl_speed_hz = 100000,
    };
    ESP_ERROR_CHECK(
        i2c_master_bus_add_device(bus_handle, &device_config, &aht20_device)
    );

    const uint8_t initialize[] = {0xBE, 0x08, 0x00};
    ESP_ERROR_CHECK(
        i2c_master_transmit(aht20_device, initialize, sizeof(initialize), 1000)
    );
    vTaskDelay(pdMS_TO_TICKS(20));
}

static esp_err_t aht20_read(float *temperature, float *humidity)
{
    const uint8_t trigger[] = {0xAC, 0x33, 0x00};
    uint8_t data[7] = {0};
    esp_err_t result = i2c_master_transmit(
        aht20_device,
        trigger,
        sizeof(trigger),
        1000
    );
    if (result != ESP_OK) {
        return result;
    }

    vTaskDelay(pdMS_TO_TICKS(90));
    result = i2c_master_receive(aht20_device, data, sizeof(data), 1000);
    if (result != ESP_OK) {
        return result;
    }
    if ((data[0] & 0x80) != 0) {
        return ESP_ERR_TIMEOUT;
    }

    uint32_t raw_humidity =
        ((uint32_t)data[1] << 12)
        | ((uint32_t)data[2] << 4)
        | ((uint32_t)data[3] >> 4);
    uint32_t raw_temperature =
        (((uint32_t)data[3] & 0x0F) << 16)
        | ((uint32_t)data[4] << 8)
        | data[5];

    *humidity = (float)raw_humidity * 100.0f / 1048576.0f;
    *temperature = (float)raw_temperature * 200.0f / 1048576.0f - 50.0f;
    return ESP_OK;
}

static void sensor_task(void *arg)
{
    char payload[96];
    while (true) {
        float temperature;
        float humidity;
        if (aht20_read(&temperature, &humidity) == ESP_OK) {
            snprintf(
                payload,
                sizeof(payload),
                "{\"temperature\":%.1f,\"humidity\":%.1f}",
                temperature,
                humidity
            );
            esp_mqtt_client_publish(
                mqtt_client,
                ENVIRONMENT_TOPIC,
                payload,
                0,
                1,
                false
            );
            ESP_LOGI(TAG, "环境数据：%s", payload);
        } else {
            ESP_LOGW(TAG, "AHT20 读取失败");
        }
        vTaskDelay(pdMS_TO_TICKS(CONFIG_SMARTHOME_SENSOR_INTERVAL_SECONDS * 1000));
    }
}

void app_main(void)
{
    esp_err_t nvs_result = nvs_flash_init();
    if (nvs_result == ESP_ERR_NVS_NO_FREE_PAGES
        || nvs_result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    gpio_config_t fan_gpio = {
        .pin_bit_mask = 1ULL << CONFIG_SMARTHOME_FAN_GPIO,
        .mode = GPIO_MODE_INPUT_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&fan_gpio));
    set_fan(false);

    aht20_init();
    wifi_init();
    xEventGroupWaitBits(
        wifi_event_group,
        WIFI_CONNECTED_BIT,
        pdFALSE,
        pdTRUE,
        portMAX_DELAY
    );
    mqtt_init();
    xTaskCreate(sensor_task, "sensor_task", 4096, NULL, 5, NULL);
}

