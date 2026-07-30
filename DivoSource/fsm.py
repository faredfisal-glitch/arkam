from aiogram.fsm.state import State, StatesGroup

class AddAccountState(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()

class JoinState(StatesGroup):
    waiting_for_link = State()
    waiting_for_count = State()

class LeaveState(StatesGroup):
    waiting_for_link = State()
    waiting_for_count = State()

class InviteState(StatesGroup):
    waiting_for_link = State()
    waiting_for_count = State()

class Disable2FAState(StatesGroup):
    waiting_for_password = State()

class Enable2FAState(StatesGroup):
    waiting_for_password = State()

class Change2FAState(StatesGroup):
    waiting_for_old_password = State()
    waiting_for_new_password = State()

class AddSellerState(StatesGroup):
    waiting_for_id = State()

class RemoveResellerState(StatesGroup):
    waiting_for_id = State()

class AddBuyerState(StatesGroup):
    waiting_for_id = State()
    waiting_for_limit = State()

class RemoveBuyerState(StatesGroup):
    waiting_for_id = State()

class ReportState(StatesGroup):
    waiting_for_link = State()
    waiting_for_type = State()
    waiting_for_reason = State()

class ReportUserState(StatesGroup):
    waiting_for_username = State()

class SessionState(StatesGroup):
    waiting_for_type = State()
    waiting_for_number = State()
    waiting_for_archive = State()

class TransferState(StatesGroup):
    waiting_for_source = State()
    waiting_for_target = State()
    waiting_for_file = State()
    waiting_for_count = State()
    waiting_for_adds_per_acc = State()

class FetchToFileState(StatesGroup):
    waiting_for_type = State()
    waiting_for_source = State()
    waiting_for_count = State()

class EditAccountState(StatesGroup):
    waiting_for_first_name = State()
    waiting_for_last_name = State()
    waiting_for_username = State()
    waiting_for_bio = State()
    waiting_for_photo = State()
    waiting_for_story = State()

class YastahaqqVoteState(StatesGroup):
    waiting_for_link = State()
    waiting_for_count = State()

class NormalVoteState(StatesGroup):
    waiting_for_link = State()
    waiting_for_count = State()

class ReactionState(StatesGroup):
    waiting_for_link = State()
    waiting_for_emoji = State()
    waiting_for_count = State()

class BackupState(StatesGroup):
    waiting_for_backup_file = State()

class LoginEmailState(StatesGroup):
    waiting_for_email = State()
    waiting_for_code = State()

class UserLoginState(StatesGroup):
    waiting_for_contact = State()
    waiting_for_code = State()
    waiting_for_password = State()

