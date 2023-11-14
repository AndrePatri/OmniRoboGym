import torch
import numpy as np

from omni.isaac.sensor import ContactSensor

from omni_robo_gym.utils.defs import Journal

from typing import List, Dict

from omni.isaac.core.world import World

class OmniContactSensors:

    def __init__(self, 
            name: str, # robot name for which contact sensors are to be created
            n_envs: int, # number of environments
            contact_prims: Dict[str, List] = None,
            contact_offsets: Dict[str, Dict[str, np.ndarray]] = None,
            sensor_radii: Dict[str, Dict[str, np.ndarray]] = None,
            device = "cuda",
            dtype = torch.float64):

        # contact sensors abstraction for a single robot
        # over multiple environments

        self.n_envs = n_envs

        self.device = device
        self.dtype = dtype

        self.journal = Journal()

        self.name = name

        self.contact_radius_default = 0.003
        
        # parses contact dictionaries and checks for issues
        self._parse_contact_dicts(self.name, 
                            contact_prims, 
                            contact_offsets, 
                            sensor_radii)

        self.n_sensors = len(self.contact_prims)

        self.in_contact = torch.full((n_envs, self.n_sensors), 
                    False, 
                    device = self.device, 
                    dtype=torch.bool)
        
        self.force_norm = torch.full((n_envs, self.n_sensors), 
                    -1.0, 
                    device = self.device, 
                    dtype=self.dtype)

        self.n_contacts = torch.full((n_envs, self.n_sensors), 
                    0, 
                    device = self.device, 
                    dtype=torch.int)

        self.contact_sensors = [[None] * self.n_sensors] * n_envs # outer: environment, 
        # inner: contact sensor, ordered as in contact_prims

    def _parse_contact_dicts(self, 
                            name: str,
                            contact_prims: Dict[str, List],
                            contact_offsets: Dict[str, Dict[str, np.ndarray]],
                            sensor_radii: Dict[str, Dict[str, np.ndarray]]):
        
        try:

            self.contact_prims = contact_prims[name]

        except:
            
            exception = f"[{self.__class__.__name__}]" + f"[{self.journal.exception}]" + \
                f"Could not find key {name} in" + \
                "contact_prims dictionary."
            
            raise Exception(exception)
        
        try:

            self.contact_offsets = contact_offsets[name]

        except:
            
            exception = f"[{self.__class__.__name__}]" + f"[{self.journal.exception}]" + \
                f"Could not find key {name} in" + \
                "contact_offsets dictionary."
            
            raise Exception(exception)
        
        try:

            self.sensor_radii = sensor_radii[name]

        except:
            
            exception = f"[{self.__class__.__name__}]" + f"[{self.journal.exception}]" + \
                f"Could not find key {name} in" + \
                "sensor_radii dictionary."
            
            raise Exception(exception)
        
        contact_offsets_ok = all(item in self.contact_offsets for item in self.contact_prims)
        sensor_radii_ok = all(item in self.sensor_radii for item in self.contact_prims)

        if not contact_offsets_ok:

            exception = f"[{self.__class__.__name__}]" + f"[{self.journal.warning}]" + \
                f"Provided contact_offsets dictionary does not posses all the necessary keys. " + \
                f"It should contain all of [{' '.join(self.contact_prims)}]. \n" + \
                f"Resetting all offsets to zero..."
            
            print(exception)

            for i in range(0, len(self.contact_prims)):

                self.contact_offsets[self.contact_prims[i]] = np.array([0.0, 0.0, 0.0])

        if not sensor_radii_ok:

            exception = f"[{self.__class__.__name__}]" + f"[{self.journal.warning}]" + \
                f"Provided sensor_radii dictionary does not posses all the necessary keys. " + \
                f"It should contain all of [{' '.join(self.contact_prims)}]. \n" + \
                f"Resetting all radii to {self.contact_radius_default} ..."
            
            print(exception)

            for i in range(0, len(self.contact_prims)):

                self.sensor_radii[self.contact_prims[i]] = self.contact_radius_default

    def create_base_contact_sensor(self, 
                    world: World, 
                    envs_namespace: str):

        robot_name = self.name
        contact_link_names = self.contact_prims
        
        for env_idx in range(0, self.n_envs):
        # env_idx = 0 # create contact sensors for base env only 

            for sensor_idx in range(0, self.n_sensors):
                
                contact_link_prim_path = envs_namespace + f"/env_{env_idx}" + \
                    "/" + robot_name + \
                        "/" + contact_link_names[sensor_idx]

                sensor_prim_path = contact_link_prim_path + \
                            "/contact_sensor" # contact sensor prim path

                print(f"[{self.__class__.__name__}]" + f"[{self.journal.status}]" + ": creating contact sensor at " + 
                            f"{sensor_prim_path}...")

                contact_sensor = ContactSensor(
                            prim_path=sensor_prim_path,
                            name=f"{robot_name}{env_idx}_{contact_link_names[sensor_idx]}_contact_sensor",
                            min_threshold=0,
                            max_threshold=10000000,
                            radius=self.sensor_radii[contact_link_names[sensor_idx]], 
                            translation=self.contact_offsets[contact_link_names[sensor_idx]], 
                            position=None
                            )
                            
                        #     world.scene.add(
                        
                        # )

                self.contact_sensors[env_idx][sensor_idx] = world.scene.add(contact_sensor)
                self.contact_sensors[env_idx][sensor_idx].add_raw_contact_data_to_frame()

                print(f"[{self.__class__.__name__}]" + f"[{self.journal.status}]" + ": contact sensor at " + 
                            f"{sensor_prim_path} created.")

    # def create_other_contact_sensors(self, 
    #                 world: World, 
    #                 envs_namespace: str):

    #     robot_name = self.name
    #     contact_link_names = self.contact_prims
        
    #     env_idx = 0 # create contact sensors for base env only 
    #     for env_idx in range(1, self.n_envs):

    #         for sensor_idx in range(0, self.n_sensors):
                
    #             contact_link_prim_path = envs_namespace + f"/env_{env_idx}" + \
    #                 "/" + robot_name + \
    #                     "/" + contact_link_names[sensor_idx]

    #             sensor_prim_path = contact_link_prim_path + \
    #                         "/contact_sensor" # contact sensor prim path

    #             print(f"[{self.__class__.__name__}]" + f"[{self.journal.status}]" + ": creating contact sensor at " + 
    #                         f"{sensor_prim_path}...")

    #             contact_sensor = ContactSensor(
    #                         prim_path=sensor_prim_path,
    #                         name=f"{robot_name}{env_idx}_{contact_link_names[sensor_idx]}_contact_sensor",
    #                         min_threshold=0,
    #                         max_threshold=10000000,
    #                         radius=self.sensor_radii[contact_link_names[sensor_idx]], 
    #                         translation=self.contact_offsets[contact_link_names[sensor_idx]], 
    #                         position=None
    #                         )
                            
    #                     #     world.scene.add(
                        
    #                     # )

    #             self.contact_sensors[env_idx][sensor_idx] = world.scene.add(contact_sensor)
    #             self.contact_sensors[env_idx][sensor_idx].add_raw_contact_data_to_frame()

    #             print(f"[{self.__class__.__name__}]" + f"[{self.journal.status}]" + ": contact sensor at " + 
    #                         f"{sensor_prim_path} created.")

    def update(self):
        
        # fill contact tensors
        
        a = 1