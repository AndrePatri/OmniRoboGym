# Copyright (C) 2023  Andrea Patrizi (AndrePatri, andreapatrizi1b6e6@gmail.com)
# 
# This file is part of OmniRoboGym and distributed under the General Public License version 2 license.
# 
# OmniRoboGym is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# 
# OmniRoboGym is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with OmniRoboGym.  If not, see <http://www.gnu.org/licenses/>.
# 
import torch 

from typing import List
from enum import Enum

from omni.isaac.core.articulations.articulation_view import ArticulationView

from omni_robo_gym.utils.defs import Journal

class FirstOrderFilter:

    # a class implementing a simple first order filter

    def __init__(self,
            dt: float, 
            filter_BW: float = 0.1, 
            rows: int = 1, 
            cols: int = 1, 
            device = "cuda",
            dtype = torch.double):
        
        self.torch_dtype = dtype

        self.journal = Journal()

        self._device = device

        self._dt = dt

        self._rows = rows
        self._cols = cols

        self._filter_BW = filter_BW

        import math 
        self._gain = 2 * math.pi * self._filter_BW

        self.yk = torch.zeros((self._rows, self._cols), device = self._device, 
                                dtype=self.torch_dtype)
        self.ykm1 = torch.zeros((self._rows, self._cols), device = self._device, 
                                dtype=self.torch_dtype)
        
        self.refk = torch.zeros((self._rows, self._cols), device = self._device, 
                                dtype=self.torch_dtype)
        self.refkm1 = torch.zeros((self._rows, self._cols), device = self._device, 
                                dtype=self.torch_dtype)
        
        self._kh2 = self._gain * self._dt / 2.0
        self._coeff_ref = self._kh2 * 1/ (1 + self._kh2)
        self._coeff_km1 = (1 - self._kh2) / (1 + self._kh2)

    def update(self, 
               refk: torch.Tensor = None):
        
        if refk is not None:

            self.refk[:, :] = refk

        self.yk[:, :] = torch.add(torch.mul(self.ykm1, self._coeff_km1), 
                            torch.mul(torch.add(self.refk, self.refkm1), 
                                        self._coeff_ref))

        self.refkm1[:, :] = self.refk
        self.ykm1[:, :] = self.yk
    
    def reset(self):

        self.yk[:, :] = torch.zeros((self._rows, self._cols), 
                            device = self._device, 
                            dtype=self.torch_dtype)
        
        self.ykm1[:, :] = torch.zeros((self._rows, self._cols), 
                            device = self._device, 
                            dtype=self.torch_dtype)
        
        self.refk[:, :] = torch.zeros((self._rows, self._cols), 
                            device = self._device, 
                            dtype=self.torch_dtype)
        
        self.refkm1[:, :] = torch.zeros((self._rows, self._cols), 
                            device = self._device, 
                            dtype=self.torch_dtype)
    
    def get(self):

        return self.yk
    
class OmniJntImpCntrl:

    # Exploits IsaacSim's low level articulation joint impedance controller

    class IndxState(Enum):

        NONE = -1 
        VALID = 1
        INVALID = 0

    def __init__(self, 
                articulation: ArticulationView,
                default_pgain = 300.0, 
                default_vgain = 30.0, 
                backend = "torch", 
                device: torch.device = torch.device("cpu"), 
                filter_BW = 50.0, # [Hz]
                filter_dt = None, 
                override_art_controller = False,
                init_on_creation = False, 
                dtype = torch.double): # [s]
        
        self.torch_dtype = dtype

        self.override_art_controller = override_art_controller # whether to override Isaac's internal joint
        # articulation PD controller or not

        self.init_on_creation = init_on_creation # init. articulation's gains and refs as soon as the controller
        # is created

        self.gains_initialized = False
        self.refs_initialized = False

        self._default_pgain = default_pgain
        self._default_vgain = default_vgain

        self._filter_BW = filter_BW
        self._filter_dt = filter_dt
        
        self.journal = Journal() # for logging
        
        self._articulation_view = articulation # used to actually apply control
        # signals to the robot

        if not self._articulation_view.initialized:

            raise Exception(f"[{self.__class__.__name__}]" + \
                            f"[{self.journal.exception}]" + \
                            ": the provided articulation_view is not initialized properly!!")

        self._valid_signal_types = ["pos_ref", "vel_ref", "eff_ref", # references 
                                    "pos", "vel", "eff", # measurements (necessary if overriding Isaac's art. controller)
                                    "pgain", "vgain"] 
        
        self._device = device

        self.num_robots = self._articulation_view.count
        self.n_dofs = self._articulation_view.num_dof
        self.jnts_names = self._articulation_view.dof_names

        self.jnt_idxs = torch.tensor([i for i in range(0, self.n_dofs)], 
                                    device = self._device, 
                                    dtype=torch.int64)

        if (backend != "torch"):

            print(f"[{self.__class__.__name__}]"  + f"[{self.journal.info}]" + ": forcing torch backend. Other backends are not yet supported.")
        
        self._backend = "torch"

        self._pos_err = None
        self._vel_err = None
        
        self._pos = None
        self._vel = None
        self._eff = None
        
        self._imp_eff = None

        self._filter_available = False

        if filter_dt is not None:

            self._filter_BW = filter_BW
            self._filter_dt = filter_dt

            self._pos_ref_filter = FirstOrderFilter(dt=self._filter_dt, 
                                    filter_BW=self._filter_BW, 
                                    rows=self.num_robots, 
                                    cols=self.n_dofs, 
                                    device=self._device, 
                                    dtype=self.torch_dtype)
            self._vel_ref_filter = FirstOrderFilter(dt=self._filter_dt, 
                                    filter_BW=self._filter_BW, 
                                    rows=self.num_robots, 
                                    cols=self.n_dofs, 
                                    device=self._device, 
                                    dtype=self.torch_dtype)
            self._eff_ref_filter = FirstOrderFilter(dt=self._filter_dt, 
                                    filter_BW=self._filter_BW, 
                                    rows=self.num_robots, 
                                    cols=self.n_dofs, 
                                    device=self._device, 
                                    dtype=self.torch_dtype)
            
            self._filter_available = True

        else:

            print(f"[{self.__class__.__name__}]"  + f"[{self.journal.warning}]" + \
                    ": no filter dt provided -> reference filter will not be available")
                                      
        self.reset()
                
    def _initialize_gains(self):
        
        if not self.gains_initialized:
            
            if not self.override_art_controller:
        
                self._articulation_view.set_gains(kps = self._pos_gains, 
                                        kds = self._vel_gains)

            else:
                
                # settings Isaac's PD controller gains to 0

                no_gains = torch.zeros((self.num_robots, self.n_dofs), device = self._device, 
                                    dtype=self.torch_dtype)
                                                  
                self._articulation_view.set_gains(kps = no_gains, 
                                    kds = no_gains)
            
            self.gains_initialized = True

    def _initialize_refs(self):

        if not self.refs_initialized: 
            
            if not self.override_art_controller:
        
                self._articulation_view.set_joint_efforts(self._eff_ref)
                
                self._articulation_view.set_joint_position_targets(self._pos_ref)

                self._articulation_view.set_joint_velocity_targets(self._vel_ref)

            else:
                
                self._articulation_view.set_joint_efforts(self._eff_ref)
    
            self.refs_initialized = True
    
    def _validate_selectors(self, 
                robot_indxs: torch.Tensor = None, 
                jnt_indxs: torch.Tensor = None):

        check = [None] * 2 

        if robot_indxs is not None:

            robot_indxs_shape = robot_indxs.shape

            if (not (len(robot_indxs_shape) == 1 and \
                robot_indxs.dtype == torch.int64 and \
                robot_indxs.device.type == self._device.type and \
                bool(torch.min(robot_indxs) >= 0) and \
                bool(torch.max(robot_indxs) < self.num_robots))): # sanity checks 

                check[0] = OmniJntImpCntrl.IndxState.INVALID

                print(f"[{self.__class__.__name__}]"  + \
                    f"[{self.journal.warning}]" + f"[{self._validate_selectors.__name__}]" + \
                    ": mismatch in provided selector ->")
                print(f"[{self.__class__.__name__}]"  + \
                    f"[{self.journal.warning}]" + f"[{self._validate_selectors.__name__}]" + \
                    ": robot_indxs_shape -> " + f"{len(robot_indxs_shape)}" + " VS" + " expected -> " + f"{1}")
                print(f"[{self.__class__.__name__}]"  + \
                    f"[{self.journal.warning}]" + f"[{self._validate_selectors.__name__}]" + \
                    ": robot_indxs.dtype -> " + f"{robot_indxs.dtype}" + " VS" + " expected -> " + f"{torch.int64}")
                print(f"[{self.__class__.__name__}]"  + \
                    f"[{self.journal.warning}]" + f"[{self._validate_selectors.__name__}]" + \
                    ": robot_indxs.device -> " + f"{robot_indxs.device.type}" + " VS" + " expected -> " + f"{self._device.type}")
                print(f"[{self.__class__.__name__}]"  + \
                    f"[{self.journal.warning}]" + f"[{self._validate_selectors.__name__}]" + \
                    ": torch.min(robot_indxs) >= 0) -> " + \
                        f"{bool(torch.min(robot_indxs) >= 0)}" + " VS" + f" {True}")
                print(f"[{self.__class__.__name__}]"  + \
                    f"[{self.journal.warning}]" + f"[{self._validate_selectors.__name__}]" + \
                    ": torch.max(robot_indxs) < self.n_dofs -> " + f"{torch.max(robot_indxs)}" + " VS" + f" {self.num_robots}")
            else:

                check[0] = OmniJntImpCntrl.IndxState.VALID

        else:

            check[0] = OmniJntImpCntrl.IndxState.NONE

        if jnt_indxs is not None:

            jnt_indxs_shape = jnt_indxs.shape

            if (not (len(jnt_indxs_shape) == 1 and \
                jnt_indxs.dtype == torch.int64 and \
                jnt_indxs.device.type == self._device.type and \
                bool(torch.min(jnt_indxs) >= 0) and \
                bool(torch.max(jnt_indxs) < self.n_dofs))): # sanity checks 

                check[1] = OmniJntImpCntrl.IndxState.INVALID

                print(f"[{self.__class__.__name__}]"  + f"[{self.journal.warning}]" + f"[{self._validate_selectors.__name__}]" + \
                    ": mismatch in provided selector ->")
                print(f"[{self.__class__.__name__}]"  + f"[{self.journal.warning}]" + f"[{self._validate_selectors.__name__}]" + \
                    ": jnt_indxs_shape -> " + f"{len(jnt_indxs_shape)}" + " VS" + " expected -> " + f"{1}")
                print(f"[{self.__class__.__name__}]"  + f"[{self.journal.warning}]" + f"[{self._validate_selectors.__name__}]" + \
                    ": jnt_indxs.dtype -> " + f"{jnt_indxs.dtype}" + " VS" + " expected -> " + f"{torch.int64}")
                print(f"[{self.__class__.__name__}]"  + f"[{self.journal.warning}]" + f"[{self._validate_selectors.__name__}]" + \
                    ": jnt_indxs.device -> " + f"{jnt_indxs.device.type}" + " VS" + " expected -> " + f"{self._device.type}")
                print(f"[{self.__class__.__name__}]"  + f"[{self.journal.warning}]" + f"[{self._validate_selectors.__name__}]" + \
                    ": torch.min(jnt_indxs) >= 0) -> " + f"{bool(torch.min(jnt_indxs) >= 0)}" + " VS" + f" {True}")
                print(f"[{self.__class__.__name__}]"  + f"[{self.journal.warning}]" + f"[{self._validate_selectors.__name__}]" + \
                    ": torch.max(jnt_indxs) < self.n_dofs -> " + f"{torch.max(jnt_indxs)}" + " VS" + f" {self.n_dofs}")
            
            else:

                check[1] = OmniJntImpCntrl.IndxState.VALID

        else:

            check[1] = OmniJntImpCntrl.IndxState.NONE

        return check
    
    def _gen_selector(self, 
                robot_indxs: torch.Tensor, 
                jnt_indxs: torch.Tensor):
        
        selector = None

        indxs_check = self._validate_selectors(robot_indxs=robot_indxs, 
                            jnt_indxs=jnt_indxs)         

        if (indxs_check[0] == OmniJntImpCntrl.IndxState.VALID and \
            indxs_check[1] == OmniJntImpCntrl.IndxState.VALID):

            selector = torch.meshgrid((robot_indxs, jnt_indxs), 
                                    indexing="ij")
        
        if(indxs_check[0] == OmniJntImpCntrl.IndxState.VALID and \
           indxs_check[1] == OmniJntImpCntrl.IndxState.NONE):
            
            selector = torch.meshgrid((robot_indxs, 
                                       torch.tensor([i for i in range(0, self.n_dofs)], 
                                                    device = self._device, 
                                                    dtype=torch.int64)), 
                                    indexing="ij")
        
        if(indxs_check[0] == OmniJntImpCntrl.IndxState.NONE and \
           indxs_check[1] == OmniJntImpCntrl.IndxState.VALID):
            
            selector = torch.meshgrid((torch.tensor([i for i in range(0, self.num_robots)], 
                                                    device = self._device, 
                                                    dtype=torch.int64)), 
                                        jnt_indxs, 
                                    indexing="ij")
        
        return selector
            
    def _validate_signal(self, 
                        signal: torch.Tensor, 
                        selector: torch.Tensor = None):
        
        signal_shape = signal.shape
        if selector is None:

            if signal_shape[0] == self.num_robots and \
                signal_shape[1] == self.n_dofs and \
                signal.device.type == self._device.type:
                
                return True
            
            else:
                
                print(f"[{self.__class__.__name__}]"  + \
                    f"[{self.journal.warning}]" + f"[{self._validate_signal.__name__}]" + \
                    ": mismatch in provided signal ->")
                print(f"[{self.__class__.__name__}]"  + \
                    f"[{self.journal.warning}]" + f"[{self._validate_signal.__name__}]" + \
                    ": signal rows -> " + f"{signal_shape[0]}" + " VS" + " expected rows -> " + \
                        f"{self.num_robots}")
                print(f"[{self.__class__.__name__}]"  + \
                    f"[{self.journal.warning}]" + f"[{self._validate_signal.__name__}]" + \
                    ": signal cols -> " + f"{signal_shape[1]}" + " VS" + " expected cols -> " + \
                        f"{self.n_dofs}")
                print(f"[{self.__class__.__name__}]"  + \
                    f"[{self.journal.warning}]" + f"[{self._validate_signal.__name__}]" + \
                    ": signal device -> " + f"{signal.device.type}" + " VS" + " expected type -> " + \
                        f"{self._device.type}")

                return False
            
        else:
            
            selector_shape = selector[0].shape

            if signal_shape[0] == selector_shape[0] and \
                signal_shape[1] == selector_shape[1] and \
                signal.device.type == self._device.type:

                return True
            
            else:
                
                print(f"[{self.__class__.__name__}]"  + \
                    f"[{self.journal.warning}]" + f"[{self._validate_signal.__name__}]" + \
                    ": mismatch in provided signal and/or selector ->")
                print(f"[{self.__class__.__name__}]"  + \
                    f"[{self.journal.warning}]" + f"[{self._validate_signal.__name__}]" + \
                    ": signal rows -> " + f"{signal_shape[0]}" + " VS" + " selector rows -> " + \
                        f"{selector_shape[0]}")
                print(f"[{self.__class__.__name__}]"  + \
                    f"[{self.journal.warning}]" + f"[{self._validate_signal.__name__}]" + \
                    ": signal cols -> " + f"{signal_shape[1]}" + " VS" + " selector cols -> " + \
                        f"{selector_shape[1]}")
                print(f"[{self.__class__.__name__}]"  + \
                    f"[{self.journal.warning}]" + f"[{self._validate_signal.__name__}]" + \
                    ": signal device -> " + f"{signal.device.type}" + " VS" + " expected type -> " + \
                        f"{self._device.type}")

                return False
        
    def _assign_signal(self, 
                    signal_type: str,
                    signal: torch.Tensor = None, 
                    selector: torch.Tensor = None):

        if signal_type in self._valid_signal_types: 

            if signal_type == self._valid_signal_types[0]: # "pos_ref"
                
                if selector is not None:

                    self._pos_ref[selector] = signal

                else:
                    
                    self._pos_ref[:, :] = signal

                return True

            if signal_type == self._valid_signal_types[1]: # "vel_ref"
                
                if selector is not None:

                    self._vel_ref[selector] = signal

                else:
                    
                    self._vel_ref[:, :] = signal

                return True

            if signal_type == self._valid_signal_types[2]: # "eff_ref"

                if selector is not None:

                    self._eff_ref[selector] = signal

                else:
                    
                    self._eff_ref[:, :] = signal
                
                return True

            if signal_type == self._valid_signal_types[3]: # "pos"
                
                if selector is not None:

                    self._pos[selector] = signal

                else:
                    
                    self._pos[:, :] = signal

                return True
            
            if signal_type == self._valid_signal_types[4]: # "vel"
                
                if selector is not None:

                    self._vel[selector] = signal

                else:
                    
                    self._vel[:, :] = signal

                return True
            
            if signal_type == self._valid_signal_types[5]: # "eff"
                
                if selector is not None:

                    self._eff[selector] = signal

                else:
                    
                    self._eff[:, :] = signal

                return True
            
            if signal_type == self._valid_signal_types[6]: # "pgain"
                
                if selector is not None:
                    
                    self._pos_gains[selector] = signal

                else:
                    
                    self._pos_gains[:, :] = signal

                return True

            if signal_type == self._valid_signal_types[7]: # "vgain"
                
                if selector is not None:

                    self._vel_gains[selector] = signal

                else:
                    
                    self._vel_gains[:, :] = signal

                return True

        else:

            return False
    
    def reset(self):
        
        self.gains_initialized = False
        self.refs_initialized = False
        
        # we assume diagonal joint impedance gain matrices, so we can save on memory and only store the diagonal
        
        self._pos_gains =  torch.full((self.num_robots, self.n_dofs), 
                                    self._default_pgain, 
                                    device = self._device, 
                                    dtype=self.torch_dtype)
        self._vel_gains = torch.full((self.num_robots, self.n_dofs), 
                                    self._default_vgain,
                                    device = self._device, 
                                    dtype=self.torch_dtype)
        
        self._eff_ref = torch.zeros((self.num_robots, self.n_dofs), device = self._device, 
                                    dtype=self.torch_dtype)
        self._pos_ref = torch.zeros((self.num_robots, self.n_dofs), device = self._device, 
                                    dtype=self.torch_dtype)
        self._vel_ref = torch.zeros((self.num_robots, self.n_dofs), device = self._device, 
                                    dtype=self.torch_dtype)
            
        # if self.override_art_controller:
            
        # saving memory (these are not necessary if not overriding Isaac's art. controller)
                                    
        self._pos_err = torch.zeros((self.num_robots, self.n_dofs), device = self._device, 
                                    dtype=self.torch_dtype)
        self._vel_err = torch.zeros((self.num_robots, self.n_dofs), device = self._device, 
                                    dtype=self.torch_dtype)
        
        self._pos = torch.zeros((self.num_robots, self.n_dofs), device = self._device, 
                                    dtype=self.torch_dtype)
        self._vel = torch.zeros((self.num_robots, self.n_dofs), device = self._device, 
                                    dtype=self.torch_dtype)
        self._eff = torch.zeros((self.num_robots, self.n_dofs), device = self._device, 
                                    dtype=self.torch_dtype)
        
        self._imp_eff = torch.zeros((self.num_robots, self.n_dofs), device = self._device, 
                                    dtype=self.torch_dtype)

        if self._filter_dt is not None:

            self._pos_ref_filter = FirstOrderFilter(dt=self._filter_dt, 
                                    filter_BW=self._filter_BW, 
                                    rows=self.num_robots, 
                                    cols=self.n_dofs, 
                                    device=self._device, 
                                    dtype=self.torch_dtype)
            self._vel_ref_filter = FirstOrderFilter(dt=self._filter_dt, 
                                    filter_BW=self._filter_BW, 
                                    rows=self.num_robots, 
                                    cols=self.n_dofs, 
                                    device=self._device, 
                                    dtype=self.torch_dtype)
            self._eff_ref_filter = FirstOrderFilter(dt=self._filter_dt, 
                                    filter_BW=self._filter_BW, 
                                    rows=self.num_robots, 
                                    cols=self.n_dofs, 
                                    device=self._device, 
                                    dtype=self.torch_dtype)
            
            self._pos_ref_filter.reset()
            self._vel_ref_filter.reset()
            self._eff_ref_filter.reset()

        if self.init_on_creation:
        
            self._initialize_gains()

            self._initialize_refs()
        
    def update_state(self, 
        pos: torch.Tensor = None, 
        vel: torch.Tensor = None, 
        eff: torch.Tensor = None,
        robot_indxs: torch.Tensor = None, 
        jnt_indxs: torch.Tensor = None):

        success = [True] * 4 # error codes:
        # success[0] == False -> pos error
        # success[1] == False -> vel error
        # success[2] == False -> eff error
        # success[3] == False -> assign error

        # if self.override_art_controller:
                                      
        selector = self._gen_selector(robot_indxs=robot_indxs, 
                        jnt_indxs=jnt_indxs)
        
        if pos is not None:

            valid = self._validate_signal(signal = pos, 
                                        selector = selector) 
            
            if (valid):
                
                if(not self._assign_signal(signal_type = "pos", 
                                    signal = pos, 
                                    selector = selector)):
                    
                    success[3] = False
            
            else:

                success[0] = False 

        if vel is not None:

            valid = self._validate_signal(signal = vel, 
                                        selector = selector) 
            
            if (valid):
                
                if(not self._assign_signal(signal_type = "vel", 
                                    signal = vel, 
                                    selector = selector)):
                    
                    success[3] = False
        
            else:

                success[1] = False

        if eff is not None:

            valid = self._validate_signal(signal = eff, 
                                        selector = selector) 
            
            if (valid):
                
                if(not self._assign_signal(signal_type = "eff", 
                                    signal = eff, 
                                    selector = selector)):
                    
                    success[3] = False
            
            else:

                success[2] = False
    
        return success

    def set_gains(self, 
                pos_gains: torch.Tensor = None, 
                vel_gains: torch.Tensor = None, 
                robot_indxs: torch.Tensor = None, 
                jnt_indxs: torch.Tensor = None):

        success = [True] * 3 # error codes:
        # success[0] == False -> pos_gains error
        # success[1] == False -> vel_gains error
        # success[2] == False -> assign error
        
        selector = self._gen_selector(robot_indxs=robot_indxs, 
                           jnt_indxs=jnt_indxs)
        
        if pos_gains is not None:

            pos_gains_valid = self._validate_signal(signal = pos_gains, 
                                                    selector = selector) 
            
            if (pos_gains_valid):
                
                if(not self._assign_signal(signal_type = "pgain", 
                                    signal = pos_gains, 
                                    selector=selector)):
                    
                    success[2] = False
                
                else:
                    
                    if not self.override_art_controller:
                                        
                        self._articulation_view.set_gains(kps = self._pos_gains)
            else:

                success[0] = False 

        if vel_gains is not None:

            vel_gains_valid = self._validate_signal(signal = pos_gains, 
                                                    selector = selector) 
            
            if (vel_gains_valid):
                
                if (not self._assign_signal(signal_type = "vgain", 
                                    signal = vel_gains, 
                                    selector=selector)):
                    
                    success[2] = False
                    
                else:
                    
                    if not self.override_art_controller:

                        self._articulation_view.set_gains(kds = self._vel_gains)
            
            else:

                success[1] = False 

        return success
    
    def set_refs(self, 
            eff_ref: torch.Tensor = None, 
            pos_ref: torch.Tensor = None, 
            vel_ref: torch.Tensor = None, 
            robot_indxs: torch.Tensor = None, 
            jnt_indxs: torch.Tensor = None):
        
        success = [True] * 4 # error codes:
        # success[0] == False -> eff_ref error
        # success[1] == False -> pos_ref error
        # success[2] == False -> vel_ref error
        # success[3] == False -> assign error

        selector = self._gen_selector(robot_indxs=robot_indxs, 
                           jnt_indxs=jnt_indxs)
        
        if eff_ref is not None:

            valid = self._validate_signal(signal = eff_ref, 
                                        selector = selector) 
            
            if (valid):
                
                if(not self._assign_signal(signal_type = "eff_ref", 
                                    signal = eff_ref, 
                                    selector = selector)):
                    
                    success[3] = False
                    
            else:

                success[0] = False 

        if pos_ref is not None:

            valid = self._validate_signal(signal = pos_ref, 
                                        selector = selector) 
            
            if (valid):
                
                if(not self._assign_signal(signal_type = "pos_ref", 
                                    signal = pos_ref, 
                                    selector = selector)):
                    
                    success[3] = False
                
            else:

                success[1] = False 
            
        if vel_ref is not None:

            valid = self._validate_signal(signal = vel_ref, 
                                        selector = selector) 
            
            if (valid):
                
                if(not self._assign_signal(signal_type = "vel_ref", 
                                    signal = vel_ref, 
                                    selector = selector)):
                
                    success[3] = False

            else:

                success[2] = False

        return success
        
    def apply_cmds(self, 
            filter = False):

        # initialize gains and refs if not done previously 
            
        if not self.gains_initialized:

            self._initialize_gains()
        
        if not self.refs_initialized:

            self._initialize_refs()
                
        if filter and self._filter_available:
            
            self._pos_ref_filter.update(self._pos_ref)
            self._vel_ref_filter.update(self._vel_ref)
            self._eff_ref_filter.update(self._eff_ref)

            if not self.override_art_controller:
            
                self._articulation_view.set_joint_position_targets(self._pos_ref_filter.get())
                self._articulation_view.set_joint_velocity_targets(self.vel_ref_filter.get())
                self._articulation_view.set_joint_efforts(self._eff_ref_filter.get())

            else:
                
                self._pos_err  = torch.sub(self._pos_ref_filter.get(), self._pos)

                self._vel_err = torch.sub(self.vel_ref_filter.get(), self._vel)

                self._imp_eff = torch.add(self._eff_ref_filter.get(), 
                                        torch.add(
                                            torch.mul(self._pos_gains, 
                                                    self._pos_err),
                                            torch.mul(self._vel_gains,
                                                    self._vel_err)))

                torch.cuda.synchronize()
                
                # apply only effort (comprehensive of all imp. terms)
                self._articulation_view.set_joint_efforts(self._imp_eff)

        else:

            if not self.override_art_controller:

                self._articulation_view.set_joint_efforts(self._eff_ref)
                self._articulation_view.set_joint_position_targets(self._pos_ref)
                self._articulation_view.set_joint_velocity_targets(self._vel_ref)
        
            else:
                
                self._pos_err  = torch.sub(self._pos_ref, self._pos)

                self._vel_err = torch.sub(self._vel_ref, self._vel)

                self._imp_eff = torch.add(self._eff_ref, 
                                        torch.add(
                                            torch.mul(self._pos_gains, 
                                                    self._pos_err),
                                            torch.mul(self._vel_gains,
                                                    self._vel_err)))

                torch.cuda.synchronize()

                # apply only effort (comprehensive of all imp. terms)
                self._articulation_view.set_joint_efforts(self._imp_eff)
                            
    def get_jnt_names_matching(self, 
                        name_pattern: str):

        return [jnt for jnt in self.jnts_names if name_pattern in jnt]

    def get_jnt_idxs_matching(self, 
                        name_pattern: str):

        jnts_names = self.get_jnt_names_matching(name_pattern)

        jnt_idxs = [self.jnts_names.index(jnt) for jnt in jnts_names]

        return torch.tensor(jnt_idxs, 
                            device=self._device, 
                            dtype=torch.int64)
    
    def pos_gains(self):

        return self._pos_gains[:, :]
    
    def vel_gains(self):

        return self._vel_gains[:, :]
    
    def eff_ref(self):

        return self._eff_ref[:, :]
    
    def pos_ref(self):

        return self._pos_ref[:, :]

    def vel_ref(self):

        return self._vel_ref[:, :]

    def pos_err(self):

        return self._pos_err[:, :]

    def vel_err(self):

        return self._vel_err[:, :]

    def pos(self):

        return self._pos[:, :]
    
    def vel(self):

        return self._vel[:, :]

    def eff(self):

        return self._eff[:, :]

    def imp_eff(self):

        return self._imp_eff[:, :]