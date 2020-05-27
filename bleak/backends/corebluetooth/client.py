"""
BLE Client for CoreBluetooth on macOS

Created on 2019-6-26 by kevincar <kevincarrolldavis@gmail.com>
"""

import logging
import uuid
from asyncio.events import AbstractEventLoop
from functools import partial
from typing import Callable, Any, Union

from Foundation import NSData, CBUUID
from CoreBluetooth import CBCharacteristicWriteWithResponse, CBCharacteristicWriteWithoutResponse

from bleak.backends.client import BaseBleakClient
from bleak.backends.corebluetooth import CBAPP as cbapp
from bleak.backends.corebluetooth.characteristic import (
    BleakGATTCharacteristicCoreBluetooth
)
from bleak.backends.corebluetooth.PeripheralDelegate import PeripheralDelegate
from bleak.backends.corebluetooth.descriptor import BleakGATTDescriptorCoreBluetooth
from bleak.backends.corebluetooth.discovery import discover
from bleak.backends.corebluetooth.service import BleakGATTServiceCoreBluetooth
from bleak.backends.service import BleakGATTServiceCollection
from bleak.exc import BleakError
from bleak.backends.corebluetooth.CentralManagerDelegate import CMDConnectionState


logger = logging.getLogger(__name__)


class BleakClientCoreBluetooth(BaseBleakClient):
    """CoreBluetooth class interface for BleakClient

    Args:
        address (str): The uuid of the BLE peripheral to connect to.
        loop (asyncio.events.AbstractEventLoop): The event loop to use.

    Keyword Args:
        timeout (float): Timeout for required ``discover`` call during connect. Defaults to 2.0.

    """

    def __init__(self, address: str, loop: AbstractEventLoop = None, **kwargs):
        super(BleakClientCoreBluetooth, self).__init__(address.upper(), loop, **kwargs)
        self._services = None
        self._connection_state = CMDConnectionState.DISCONNECTED
        self._peripheral = None  
        self._peripheral_delegate = None  
        self._disconnected_callback = None
        self._peripheral = None

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Call base class to cleanup (disconnect)
        await super(BleakClientCoreBluetooth, self).__aexit__(exc_type, exc_val, exc_tb)
        # Remove this from the dictionary of clients
        cbapp.central_manager_delegate.removeclient_(self)
        
    def __str__(self):
        return "BleakClientCoreBluetooth ({})".format(self.address)

    async def connect(self, **kwargs) -> bool:
        """Connect to a specified Peripheral

        Keyword Args:
            timeout (float): Timeout for required ``discover`` call. Defaults to 2.0.

        Returns:
            Boolean representing connection status.

        """
        timeout = kwargs.get("timeout", self._timeout)

        # If the peripheral isn't already known
        self._peripheral = None # TODO TEST Remove when done!
        if self._peripheral == None:
            # Use results from prior scans  
            known_devices = cbapp.central_manager_delegate.devices
            # If it's not there, search for it via discovery
            if self.address not in known_devices:
                devices = await discover(timeout=timeout, loop=self.loop)
                # Get the updates
                known_devices = cbapp.central_manager_delegate.devices
                if self.address not in known_devices:
                    raise BleakError(
                        "Device with address {} was not found".format(self.address)
                    )

            # We have it in known_devices
            self._peripheral = known_devices[self.address].details

        self._peripheral_delegate = PeripheralDelegate.alloc().initWithPeripheral_(self._peripheral)

        logger.debug("Connecting to BLE device @ {}".format(self.address))
        await cbapp.central_manager_delegate.connect_(self)

        # Now get services
        await self.get_services()
        
        return True

    async def disconnect(self) -> bool:
        """Disconnect from the peripheral device"""
        return await cbapp.central_manager_delegate.disconnect_(self)

    async def is_connected(self) -> bool:
        """Checks for current active connection"""
        return self._connection_state == CMDConnectionState.CONNECTED

    def set_disconnected_callback(
        self, callback: Callable[[BaseBleakClient], None], **kwargs
    ) -> None:
        """Set the disconnected callback.
        Args:
            callback: callback to be called on disconnection.

        """
        # Keep same args used w/ BlueZ (i.e., a future)
        f = self.loop.create_future()  
        f.set_result(None)
        self._disconnected_callback = partial(callback, self, f)

    async def get_services(self) -> BleakGATTServiceCollection:
        """Get all services registered for this GATT server.

        Returns:
           A :py:class:`bleak.backends.service.BleakGATTServiceCollection` with this device's services tree.

        """
        if self._services is not None:
            return self._services

        logger.debug("Retrieving services...")
        services = await self._peripheral_delegate.discoverServices()

        for service in services:
            serviceUUID = service.UUID().UUIDString()
            logger.debug(
                "Retrieving characteristics for service {}".format(serviceUUID)
            )
            characteristics = await self._peripheral_delegate.discoverCharacteristics_(
                service
            )

            self.services.add_service(BleakGATTServiceCoreBluetooth(service))

            for characteristic in characteristics:
                cUUID = characteristic.UUID().UUIDString()
                logger.debug(
                    "Retrieving descriptors for characteristic {}".format(cUUID)
                )
                descriptors = await self._peripheral_delegate.discoverDescriptors_(characteristic)

                self.services.add_characteristic(BleakGATTCharacteristicCoreBluetooth(characteristic))
                for descriptor in descriptors:
                    self.services.add_descriptor(
                        BleakGATTDescriptorCoreBluetooth(
                            descriptor, characteristic.UUID().UUIDString()
                        )
                    )
        self._services_resolved = True
        self._services = services
        return self.services

    async def read_gatt_char(self, _uuid: Union[str, uuid.UUID], use_cached=False, **kwargs) -> bytearray:
        """Perform read operation on the specified GATT characteristic.

        Args:
            _uuid (str or UUID): The uuid of the characteristics to read from.
            use_cached (bool): `False` forces macOS to read the value from the
                device again and not use its own cached value. Defaults to `False`.

        Returns:
            (bytearray) The read data.

        """
        _uuid = await self.get_appropriate_uuid(str(_uuid))
        characteristic = self.services.get_characteristic(str(_uuid))
        if not characteristic:
            raise BleakError("Characteristic {} was not found!".format(_uuid))

        output = await self._peripheral_delegate.readCharacteristic_(
            characteristic.obj, use_cached=use_cached
        )
        value = bytearray(output) if output.length() !=0 else bytearray()
        logger.debug("Read Characteristic {0} : {1}".format(_uuid, value))
        return value

    async def read_gatt_descriptor(
        self, handle: int, use_cached=False, **kwargs
    ) -> bytearray:
        """Perform read operation on the specified GATT descriptor.

        Args:
            handle (int): The handle of the descriptor to read from.
            use_cached (bool): `False` forces Windows to read the value from the
                device again and not use its own cached value. Defaults to `False`.

        Returns:
            (bytearray) The read data.
        """
        descriptor = self.services.get_descriptor(handle)
        if not descriptor:
            raise BleakError("Descriptor {} was not found!".format(handle))

        output = await self._peripheral_delegate.readDescriptor_(
            descriptor.obj, use_cached=use_cached
        )
        if isinstance(
            output, str
        ):  # Sometimes a `pyobjc_unicode`or `__NSCFString` is returned and they can be used as regular Python strings.
            value = bytearray(output.encode("utf-8"))
        else:  # _NSInlineData
            value = bytearray(output)  # value.getBytes_length_(None, len(value))
        logger.debug("Read Descriptor {0} : {1}".format(handle, value))
        return value

    async def write_gatt_char(
        self, _uuid: Union[str, uuid.UUID], data: bytearray, response: bool = False
    ) -> None:
        """Perform a write operation of the specified GATT characteristic.

        Args:
            _uuid (str or UUID): The uuid of the characteristics to write to.
            data (bytes or bytearray): The data to send.
            response (bool): If write-with-response operation should be done. Defaults to `False`.

        """
        _uuid = await self.get_appropriate_uuid(str(_uuid))
        characteristic = self.services.get_characteristic(str(_uuid))
        if not characteristic:
            raise BleakError("Characteristic {} was not found!".format(_uuid))

        value = NSData.alloc().initWithBytes_length_(data, len(data))
        success = await self._peripheral_delegate.writeCharacteristic_value_type_(
            characteristic.obj,
            value,
            CBCharacteristicWriteWithResponse if response else CBCharacteristicWriteWithoutResponse
        )
        if success:
            logger.debug("Write Characteristic {0} : {1}".format(_uuid, data))
        else:
            logger.debug("Write Failed")
            raise BleakError(
                "Could not write value {0} to characteristic {1}: {2}".format(
                    data, characteristic.uuid, success
                )
            )

    async def write_gatt_descriptor(self, handle: int, data: bytearray) -> None:
        """Perform a write operation on the specified GATT descriptor.

        Args:
            handle (int): The handle of the descriptor to read from.
            data (bytes or bytearray): The data to send.

        """
        descriptor = self.services.get_descriptor(handle)
        if not descriptor:
            raise BleakError("Descriptor {} was not found!".format(handle))

        value = NSData.alloc().initWithBytes_length_(data, len(data))
        success = await self._peripheral_delegate.writeDescriptor_value_(
            descriptor.obj, value
        )
        if success:
            logger.debug("Write Descriptor {0} : {1}".format(handle, data))
        else:
            raise BleakError(
                "Could not write value {0} to descriptor {1}: {2}".format(
                    data, descriptor.uuid, success
                )
            )

    async def start_notify(
        self, _uuid: Union[str, uuid.UUID], callback: Callable[[str, Any], Any], **kwargs
    ) -> None:
        """Activate notifications/indications on a characteristic.

        Callbacks must accept two inputs. The first will be a uuid string
        object and the second will be a bytearray.

        .. code-block:: python

            def callback(sender, data):
                print(f"{sender}: {data}")
            client.start_notify(char_uuid, callback)

        Args:
            _uuid (str or UUID): The uuid of the characteristics to start notification/indication on.
            callback (function): The function to be called on notification.

        """
        _uuid = await self.get_appropriate_uuid(str(_uuid))
        characteristic = self.services.get_characteristic(str(_uuid))
        if not characteristic:
            raise BleakError("Characteristic {0} not found!".format(_uuid))

        success = await self._peripheral_delegate.startNotify_cb_(
            characteristic.obj, callback
        )
        if not success:
            raise BleakError(
                "Could not start notify on {0}: {1}".format(
                    characteristic.uuid, success
                )
            )

    async def stop_notify(self, _uuid: Union[str, uuid.UUID]) -> None:
        """Deactivate notification/indication on a specified characteristic.

        Args:
            _uuid: The characteristic to stop notifying/indicating on.

        """
        _uuid = await self.get_appropriate_uuid(str(_uuid))
        characteristic = self.services.get_characteristic(str(_uuid))
        if not characteristic:
            raise BleakError("Characteristic {} not found!".format(_uuid))

        success = await self._peripheral_delegate.stopNotify_(characteristic.obj)
        if not success:
            raise BleakError(
                "Could not stop notify on {0}: {1}".format(characteristic.uuid, success)
            )

    async def get_appropriate_uuid(self, _uuid: str) -> str:
        if len(_uuid) == 4:
            return _uuid.upper()

        if await self.is_uuid_16bit_compatible(_uuid):
            return _uuid[4:8].upper()

        return _uuid.upper()

    async def is_uuid_16bit_compatible(self, _uuid: str) -> bool:
        test_uuid = "0000FFFF-0000-1000-8000-00805F9B34FB"
        test_int = await self.convert_uuid_to_int(test_uuid)
        uuid_int = await self.convert_uuid_to_int(_uuid)
        result_int = uuid_int & test_int
        return uuid_int == result_int

    async def convert_uuid_to_int(self, _uuid: str) -> int:
        UUID_cb = CBUUID.alloc().initWithString_(_uuid)
        UUID_data = UUID_cb.data()
        UUID_bytes = UUID_data.getBytes_length_(None, len(UUID_data))
        UUID_int = int.from_bytes(UUID_bytes, byteorder="big")
        return UUID_int

    async def convert_int_to_uuid(self, i: int) -> str:
        UUID_bytes = i.to_bytes(length=16, byteorder="big")
        UUID_data = NSData.alloc().initWithBytes_length_(UUID_bytes, len(UUID_bytes))
        UUID_cb = CBUUID.alloc().initWithData_(UUID_data)
        return UUID_cb.UUIDString()

    def did_disconnect(self):
        logger.debug("Disconnected from device @ {}".format(self.address))
        # Client device disconnected; TODO Call the callback
        # self._peripheral = None
        self._peripheral_delegate = None
        if self._disconnected_callback == None:
            return 
        self._disconnected_callback()
        