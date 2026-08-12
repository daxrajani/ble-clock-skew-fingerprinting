/*
 * Passive BLE advertisement logger for clock-skew fingerprinting.
 *
 * Logs every advertisement heard from every device (no filtering, no
 * duplicate suppression) as:
 *
 *   SKEW,<uptime_ms>,<mac_address>,<rssi>
 *
 * over UART. Meant to run unattended for hours - log_skew_capture.py on
 * the host captures this stream to a CSV, and estimate_skew.py later
 * fits each device's clock skew from its inter-packet-interval sequence.
 *
 * Uses k_uptime_get() (64-bit milliseconds since boot) rather than a
 * cycle-counter microsecond timestamp: BLE's mandatory 0-10ms random
 * advertising delay (advDelay, Core Spec Vol 6 Part B 4.4.1) already
 * dominates the noise floor, so millisecond resolution loses nothing
 * that matters, and 64-bit ms never overflows during a long capture
 * (a 32-bit microsecond counter would wrap in ~71 minutes).
 */

#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>

static void device_found(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,
			  struct net_buf_simple *ad)
{
	char addr_str[BT_ADDR_LE_STR_LEN];

	ARG_UNUSED(type);
	ARG_UNUSED(ad);

	bt_addr_le_to_str(addr, addr_str, sizeof(addr_str));
	printk("SKEW,%lld,%s,%d\n", k_uptime_get(), addr_str, rssi);
}

static int scanner_start(void)
{
	struct bt_le_scan_param scan_param = {
		.type     = BT_LE_SCAN_TYPE_ACTIVE,
		.options  = BT_LE_SCAN_OPT_NONE,
		.interval = BT_GAP_SCAN_FAST_INTERVAL_MIN,
		.window   = BT_GAP_SCAN_FAST_WINDOW,
	};
	int err = bt_le_scan_start(&scan_param, device_found);

	if (err) {
		printk("Start scanning failed (err %d)\n", err);
		return err;
	}
	printk("READY\n");
	return 0;
}

int main(void)
{
	int err;

	printk("Starting BLE clock-skew capture logger\n");

	err = bt_enable(NULL);
	if (err) {
		printk("Bluetooth init failed (err %d)\n", err);
		return 0;
	}

	(void)scanner_start();
	return 0;
}
