import requests
import json
import django.utils.text

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import MultipleObjectsReturned
from datetime import datetime
from requests import api
from dcim.models import Manufacturer
from dcim.models import Device, DeviceType
from netbox_cisco_support.models import CiscoDeviceTypeSupport, CiscoSupport


class Command(BaseCommand):
    help = 'Sync local devices with Cisco EoX Support API - V0.6'

    def add_arguments(self, parser):

        PLUGIN_SETTINGS = settings.PLUGINS_CONFIG.get("netbox_cisco_support", dict())

        # Named (optional) arguments
        # Allow custom manufacturer to be specified
        parser.add_argument(
            '--manufacturer',
            nargs='?',
            action='store',
            default=PLUGIN_SETTINGS.get("manufacturer", "Cisco"),
            help='Manufacturer name (default: Cisco)',
        )
    
        # Allow user to specify the type of Sync to run
        parser.add_argument(
            '--sync_type',
            nargs='?',
            choices=['all', 'devicetypes', 'devices'],
            default='all',  
            help='Specify what EoX Data to be synced from Cisco',
        )

        # Allow user to enable debugging - API output written to /tmp
        parser.add_argument(
            '--debug',
            action='store_true',
            default=False,  
            help='Dump API Responses to files in /tmp',
        )

    # Generic Function to parse the Device EoX API response for the supplied key
    def parse_eox_device_response(self, nb_obj, nb_obj_field_name, nb_obj_field_type, eox_api_response_key, eox_api_response):

        try:
            if (eox_api_response.get(eox_api_response_key) == None):
                self.stdout.write(self.style.NOTICE(eox_api_response['sr_no'] + " api response had no key - " + eox_api_response_key))
                setattr(nb_obj, nb_obj_field_name, None)
                return nb_obj
            else:
                resp_key_value = eox_api_response[eox_api_response_key]
                
                self.stdout.write(self.style.SUCCESS(eox_api_response['sr_no'] + " - " + eox_api_response_key +" - " + resp_key_value))

                if (nb_obj_field_type == 'date'):
                    if not (resp_key_value == ''):
                        resp_key_value = datetime.strptime(resp_key_value, '%Y-%m-%d').date()
                    else: 
                        resp_key_value = None

                obj_attrib_value = getattr(nb_obj, nb_obj_field_name)
                if obj_attrib_value != resp_key_value:
                    setattr(nb_obj, nb_obj_field_name, resp_key_value)
                
                return nb_obj
            
        except KeyError:
            self.stdout.write(self.style.NOTICE(eox_api_response['sr_no'] + " has no key named - " + eox_api_response_key))
            return nb_obj

    # Updates a single device with current EoX Data
    def update_device_eox_data(self, device):
        self.stdout.write(self.style.SUCCESS("Trying to update device %s" % device['sr_no']))

        # Get the device object from NetBox
        try:
            d = Device.objects.get(serial=device['sr_no'])
        except MultipleObjectsReturned:

            # Error if netbox has multiple SN's and skip updating
            self.stdout.write(self.style.NOTICE("ERROR: Multiple objects exist within Netbox with Serial Number " + device['sr_no']))
            return

        # Check if a CiscoSupport object already exists, if not, create a new one
        try:
            ds = CiscoSupport.objects.get(device=d)
        except CiscoSupport.DoesNotExist:
            ds = CiscoSupport(device=d)

        # Control variable to only save the object if something has changed
        value_changed = False

        # A "YES" string is not quite boolean :-)
        covered = True if device['is_covered'] == "YES" else False

        self.stdout.write(self.style.SUCCESS("%s - covered: %s" % (device['sr_no'], covered)))

        # Check if the Coverage in the CiscoSupport object equals API answer. If not, change it
        if ds.is_covered != covered:
            ds.is_covered = covered

        # Parse the Date values in the reponse
        ds = self.parse_eox_device_response(ds, 'warranty_end_date', 'date', 'warranty_end_date', device)
        ds = self.parse_eox_device_response(ds, 'coverage_end_date', 'date', 'covered_product_line_end_date', device)

        # Parse the String values in the reponse
        ds = self.parse_eox_device_response(ds, 'warranty_type', 'string', 'warranty_type', device)
        ds = self.parse_eox_device_response(ds, 'service_line_description', 'string', 'service_line_descr', device)
        ds = self.parse_eox_device_response(ds, 'service_contract_number', 'string', 'service_contract_number', device)
        ds = self.parse_eox_device_response(ds, 'contract_site_customer_name', 'string', 'contract_site_customer_name', device)
        ds = self.parse_eox_device_response(ds, 'contract_site_address1', 'string', 'contract_site_address1', device)
        ds = self.parse_eox_device_response(ds, 'contract_site_city', 'string', 'contract_site_city', device)
        ds = self.parse_eox_device_response(ds, 'contract_site_state_province', 'string', 'contract_site_state_province', device)
        ds = self.parse_eox_device_response(ds, 'contract_site_country', 'string', 'contract_site_country', device)
        
        ds.save()

        return 

    # Generic Function to parse the DeviceType EoX API response for the supplied key
    def parse_eox_devicetype_response(self, pid, nb_obj, nb_obj_field_name, eox_api_response_key, eox_api_response):

        try:
            if not eox_api_response["EOXRecord"][0][eox_api_response_key]["value"]:
                self.stdout.write(self.style.NOTICE(pid + " has no key - " + eox_api_response_key))
            else:
                resp_key_value_string = eox_api_response["EOXRecord"][0][eox_api_response_key]["value"]
                self.stdout.write(self.style.SUCCESS(pid + " - " + eox_api_response_key +" - " + resp_key_value_string))

                # Convert String Date to a datetime object
                resp_key_value = datetime.strptime(resp_key_value_string, '%Y-%m-%d').date()

                obj_attrib_value = getattr(nb_obj, nb_obj_field_name)
                if obj_attrib_value != resp_key_value:
                    setattr(nb_obj, nb_obj_field_name, resp_key_value)
                    
                return nb_obj
            
        except KeyError:
            self.stdout.write(self.style.NOTICE(pid + " has no key - " + eox_api_response_key))
            setattr(nb_obj, nb_obj_field_name, None)
    
            return nb_obj

    def update_device_type_eox_data(self, pid, eox_data):

        try:
            # Get the device type object for the supplied PID
            dt = DeviceType.objects.get(part_number=pid)

        except MultipleObjectsReturned:

            # Error if netbox has multiple PN's
            self.stdout.write(self.style.NOTICE("ERROR: Multiple objects exist within Netbox with Part Number " + pid))
            return

        # Check if CiscoDeviceTypeSupport record already exists
        try:
            dts = CiscoDeviceTypeSupport.objects.get(device_type=dt)
        # If not, create a new one for this Device Type
        except CiscoDeviceTypeSupport.DoesNotExist:
            dts = CiscoDeviceTypeSupport(device_type=dt)

        # parse_eox_devicetype_response(self, pid, nb_obj, nb_obj_field_name, eox_api_response_key, eox_api_response, value_changed):
        self.parse_eox_devicetype_response(pid, dts, "end_of_sale_date", "EndOfSaleDate", eox_data) 
        self.parse_eox_devicetype_response(pid, dts, "end_of_sw_maintenance_releases", "EndOfSWMaintenanceReleases", eox_data) 
        self.parse_eox_devicetype_response(pid, dts, "end_of_security_vul_support_date", "EndOfSecurityVulSupportDate", eox_data) 
        self.parse_eox_devicetype_response(pid, dts, "end_of_routine_failure_analysis_date", "EndOfRoutineFailureAnalysisDate", eox_data) 
        self.parse_eox_devicetype_response(pid, dts, "end_of_service_contract_renewal", "EndOfServiceContractRenewal", eox_data)
        self.parse_eox_devicetype_response(pid, dts, "last_date_of_support", "LastDateOfSupport", eox_data)
        self.parse_eox_devicetype_response(pid, dts, "end_of_svc_attach_date", "EndOfSvcAttachDate", eox_data)

        dts.save()

        return

    def get_device_types(self, manufacturer):
        # trying to get the right manufacturer for this plugin
        try:
            m = Manufacturer.objects.get(name=manufacturer)
        except Manufacturer.DoesNotExist:
            raise CommandError('Manufacturer "%s" does not exist' % manufacturer)

        self.stdout.write(self.style.SUCCESS('Found manufacturer "%s"' % m))

        # trying to get all device types and it's base PIDs associated with this manufacturer
        try:
            dt = DeviceType.objects.filter(manufacturer=m)
        except DeviceType.DoesNotExist:
            raise CommandError('Manufacturer "%s" has no Device Types' % m)

        return dt

    def get_product_ids(self, manufacturer):
        product_ids = []

        # Get all device types for supplied manufacturer
        dt = self.get_device_types(manufacturer)

        # Iterate all this device types
        for device_type in dt:

            # Skip if the device type has no valid part number. Part numbers must match the exact Cisco Base PID
            if not device_type.part_number:
                self.stdout.write(self.style.WARNING('Found device type "%s" WITHOUT Part Number - SKIPPING' % (device_type)))
                continue

            # Found Part number, append it to the list (PID collection for EoX data done)
            self.stdout.write(self.style.SUCCESS('Found device type "%s" with Part Number "%s"' % (device_type, device_type.part_number)))
            product_ids.append(device_type.part_number)

        return product_ids

    def get_serial_numbers(self, manufacturer):
        serial_numbers = []
        
        # Get all device types for supplied manufacturer
        dt = self.get_device_types(manufacturer)

        # Iterate all this device types
        for device_type in dt:
            # trying to get all devices and its serial numbers for this device type (for contract data)
            try:
                d = Device.objects.filter(device_type=device_type)

                for device in d:
                    # Skip if the device has no valid serial number.
                    if not device.serial:
                        self.stdout.write(self.style.WARNING('Found device "%s" WITHOUT Serial Number - SKIPPING' % (device)))
                        continue

                    # TODO - Add serial number to a list and do something with it rather than displaying.
                    self.stdout.write(self.style.SUCCESS('Found device "%s" with Serial Number "%s"' % (device, device.serial)))
                    serial_numbers.append(device.serial)
            except Device.DoesNotExist:
                raise CommandError('Device with device type "%s" does not exist' % dt)

        return serial_numbers

    def logon(self):
        PLUGIN_SETTINGS = settings.PLUGINS_CONFIG.get("netbox_cisco_support", dict())
        CISCO_CLIENT_ID = PLUGIN_SETTINGS.get("cisco_client_id", "")
        CISCO_CLIENT_SECRET = PLUGIN_SETTINGS.get("cisco_client_secret", "")

        token_url = "https://id.cisco.com/oauth2/default/v1/token"
        data = {'grant_type': 'client_credentials', 'client_id': CISCO_CLIENT_ID, 'client_secret': CISCO_CLIENT_SECRET}

        access_token_response = requests.post(token_url, data=data)

        tokens = json.loads(access_token_response.text)

        api_call_headers = {'Authorization': 'Bearer ' + tokens['access_token'], 'Accept': 'application/json'}

        return api_call_headers

    # Main entry point for the sync_eox_data command of manage.py
    def handle(self, *args, **kwargs):
        #PLUGIN_SETTINGS = settings.PLUGINS_CONFIG.get("netbox_cisco_support", dict())
        #MANUFACTURER = PLUGIN_SETTINGS.get("manufacturer", "Cisco")
        manufacturer = kwargs["manufacturer"]

        # Logon one time and gather the required API key
        api_call_headers = self.logon()

        if (kwargs["sync_type"] == "all" or kwargs["sync_type"] == "devicetypes"):
            # Step 1: Get all PIDs for all Device Types of that particular manufacturer
            product_ids = self.get_product_ids(manufacturer)
            self.stdout.write(self.style.SUCCESS('Gathering data for these PIDs: ' + ', '.join(product_ids)))

            i = 1
            for pid in product_ids:
                url = 'https://apix.cisco.com/supporttools/eox/rest/5/EOXByProductID/1/%s?responseencoding=json' % pid
                api_call_response = requests.get(url, headers=api_call_headers)
                self.stdout.write(self.style.SUCCESS('Call ' + url))

                # debug API answer to text file if debugging enabled
                if (kwargs["debug"] == True):
                    # sanitize file name
                    filename = django.utils.text.get_valid_filename('%s.json' % pid)

                    # write data to file
                    with open('/tmp/%s' % filename, 'w') as outfile:
                        outfile.write(api_call_response.text)

                # Validate response from Cisco 
                if api_call_response.status_code == 200:

                    # Deserialize JSON API Response into Python object "data"
                    data = json.loads(api_call_response.text)

                    # Call our Device Type Update method for that particular PID
                    self.update_device_type_eox_data(pid, data)

                    i += 1

                else:

                    # Show an error
                    self.stdout.write(self.style.ERROR('API Error: ' + api_call_response.text))


        if (kwargs["sync_type"] == "all" or kwargs["sync_type"] == "devices"):
            # Step 2: Get all Serial Numbers for all Devices of that particular manufacturer
            serial_numbers = self.get_serial_numbers(manufacturer)
            self.stdout.write(self.style.SUCCESS('Gathering data for these Serial Numbers: ' + ', '.join(serial_numbers)))

            i = 1
            while serial_numbers:
                # Pop the first items_to_fetch items of serial_numbers into current_slice and then delete them from serial
                # numbers. We want to pass x items to the API each time we call it
                items_to_fetch = 10
                current_slice = serial_numbers[:items_to_fetch]
                serial_numbers[:items_to_fetch] = []

                url = 'https://apix.cisco.com/sn2info/v2/coverage/summary/serial_numbers/%s' % ','.join(current_slice)
                api_call_response = requests.get(url, headers=api_call_headers)
                self.stdout.write(self.style.SUCCESS('Call ' + url))

                # Validate response from Cisco 
                if api_call_response.status_code == 200:

                    # Deserialize JSON API Response into Python object "data"
                    data = json.loads(api_call_response.text)

                    # debug API answer to text file if debugging enabled
                    if (kwargs["debug"] == True):

                        self.stdout.write(self.style.SUCCESS('Debugging Enabled - API Response written to log in /tmp'))

                        # sanitize file name
                        filename = django.utils.text.get_valid_filename('%s.json' % '-'.join(current_slice))

                        # write api response to file
                        with open('/tmp/%s' % filename, 'w') as outfile:
                            outfile.write(api_call_response.text)

                    # Iterate through all serial numbers included in the API response
                    for device in data['serial_numbers']:

                        # Call our Device Update method for that particular Device
                        self.update_device_eox_data(device)

                    i += 1
                
                else:

                    # Show an error
                    self.stdout.write(self.style.ERROR('API Error: ' + api_call_response.text))
