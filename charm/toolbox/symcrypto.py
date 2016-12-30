from charm.toolbox.paddingschemes import PKCS7Padding
from charm.toolbox.securerandom import OpenSSLRand
from charm.core.crypto.cryptobase import MODE_CBC,AES,selectPRP
from hashlib import sha256 as sha2
import json
import hmac
from base64 import b64encode, b64decode

class MessageAuthenticator(object):
    """ Abstraction for constructing and verifying authenticated messages

        A large number of the schemes can only encrypt group elements
        and do not provide an efficient mechanism for encoding byte in
        those elements. As such we don't pick a symmetric key and encrypt
        it asymmetrically. Rather, we hash a random group element to get the
        symmetric key.

    >>> from charm.toolbox.pairinggroup import PairingGroup,GT,extract_key
    >>> groupObj = PairingGroup('SS512')
    >>> key = groupObj.random(GT)
    >>> m = MessageAuthenticator(extract_key(key))
    >>> AuthenticatedMessage = m.mac('Hello World')
    >>> m.verify(AuthenticatedMessage)
    True
    """
    def __init__(self, key, alg="HMAC_SHA2"):
        """
        Creates a message authenticator and verifier under the specified key
        """
        if alg != "HMAC_SHA2":
            raise ValueError("Currently only HMAC_SHA2 is supported as an algorithm")
        self._algorithm = alg
        self._key = key

    def mac(self, msg, additionalData=b''):
        """
        Authenticates (MAC) a message. The MAC is computed as:
        MAC = HMAC(key, algorithm + additionalData + message).

        Parameters
        ----------
        msg : str or byte str
            The message serving as input to the HMAC algorithm, in addition to the HMAC algorithm and additional data.
        additionalData : str or byte str, optional
            Additional data that will be MACed together with the ciphertext and algorithm; the additional message will not be encrypted.

        Returns
        -------
        dict
            Dictionary composed of the MAC algorithm, the MACed message (or ciphertext), and the digest computed by MACing HMAC_algorithm + additionalData + msg.
        """
        # Ensure the additional data is in byte format, convert if necessary.
        if type(additionalData) != bytes :
            additionalData = bytes(additionalData, "utf-8")
        return {
                "alg": self._algorithm,
                "msg": msg,
                "digest": hmac.new(self._key, bytes(self._algorithm, "utf-8") + additionalData + bytes(msg, "utf-8"), digestmod=sha2).hexdigest()
               }

    def verify(self, msgAndDigest, additionalData=b''):
        """
        Verifies whether the MAC digest from input ciphertext and digest matches the computed one over ciphertext and additional data.

        Parameters
        ----------
        msgAndDigest : dict
            Dictionary composed of the MAC algorithm, the MACed message (or ciphertext), and the digest computed by MACing HMAC_algorithm + additionalData + msg.
            It is the format generated by the mac() function within this class.
        additionalData : str or byte str, optional
            Additional data that will be MACed together with the ciphertext and algorithm; the additional message will not be encrypted.

        Returns
        -------
        bool
            True if the digests match, False otherwise.

        Raises
        ------
        ValueError
            If the HMAC algorithm is not supported.
        """
        if msgAndDigest['alg'] != self._algorithm:
            raise ValueError("Currently only HMAC_SHA2 is supported as an algorithm")
        expected = bytes(self.mac(msgAndDigest['msg'], additionalData=additionalData)['digest'], 'utf-8')
        received = bytes(msgAndDigest['digest'], 'utf-8')
        # we compare the hash instead of the direct value to avoid a timing attack
        return sha2(expected).digest() == sha2(received).digest()

class SymmetricCryptoAbstraction(object):
    """
    Abstraction for symmetric encryption and decryption of data.
    Ideally provide an INDCCA2 secure symmetric container for arbitrary data.
    Currently only supports primitives that JSON can encode and decode.

    A large number of the schemes can only encrypt group elements
    and do not provide an efficient mechanism for encoding byte in
    those elements. As such we don't pick a symmetric key and encrypt
    it asymmetrically. Rather, we hash a random group element to get the
    symmetric key.

    >>> from charm.toolbox.pairinggroup import PairingGroup,GT,extract_key
    >>> groupObj = PairingGroup('SS512')
    >>> a = SymmetricCryptoAbstraction(extract_key(groupObj.random(GT)))
    >>> ct = a.encrypt(b"Friendly Fire Isn't")
    >>> a.decrypt(ct)
    b"Friendly Fire Isn't"
    """

    def __init__(self, key, alg = AES, mode = MODE_CBC):
        self._alg = alg
        self.key_len = 16
        self._block_size = 16
        self._mode = mode
        self._key = key[0:self.key_len] # expected to be bytes
        assert len(self._key) == self.key_len, "SymmetricCryptoAbstraction key too short"
        self._padding = PKCS7Padding()

    def _initCipher(self,IV = None):
        if IV == None :
            IV =  OpenSSLRand().getRandomBytes(self._block_size)
        self._IV = IV
        return selectPRP(self._alg,(self._key,self._mode,self._IV))

    def __encode_decode(self,data,func):
        data['IV'] = func(data['IV'])
        data['CipherText'] = func(data['CipherText'])
        return data

    #This code should be factored out into another class
    #Because json is only defined over strings, we need to base64 encode the encrypted data
    # and convert the base 64 byte array into a utf8 string
    def _encode(self, data):
        return self.__encode_decode(data, lambda x: b64encode(x).decode('utf-8'))

    def _decode(self, data):
        return self.__encode_decode(data, lambda x: b64decode(bytes(x, 'utf-8')))

    def encrypt(self, message):
        #This should be removed when all crypto functions deal with bytes"
        if type(message) != bytes :
            message = bytes(message, "utf-8")
        ct = self._encrypt(message)
        #JSON strings cannot have binary data in them, so we must base64 encode cipher
        cte = json.dumps(self._encode(ct))
        return cte

    def _encrypt(self, message):
        #Because the IV cannot be set after instantiation, decrypt and encrypt
        # must operate on their own instances of the cipher
        cipher = self._initCipher()
        ct= {'ALG': self._alg,
            'MODE': self._mode,
            'IV': self._IV,
            'CipherText': cipher.encrypt(self._padding.encode(message))
            }
        return ct

    def decrypt(self, cipherText):
        f = json.loads(cipherText)
        return self._decrypt(self._decode(f))

    def _decrypt(self, cipherText):
        cipher = self._initCipher(cipherText['IV'])
        msg = cipher.decrypt(cipherText['CipherText'])
        return self._padding.decode(msg)

class AuthenticatedCryptoAbstraction(SymmetricCryptoAbstraction):
    """
    Implements Authenticated Encryption with Additional Data (AEAD) abstraction. The additional data is optional, and this version is backwards compatible
    with the same class without the additional data option.

    Examples
    --------
    >>> from hashlib import sha256
    >>> import charm.toolbox.symcrypto
    >>> key = sha256(b'shameful secret key').digest()
    >>> cipher = charm.toolbox.symcrypto.AuthenticatedCryptoAbstraction(key)
    >>> ciphertext = cipher.encrypt('My age is 42.')
    >>> ciphertext
    {'digest': '0af403e93aa86cd75b0d08818b6f13deb82c1ae4bb4fb878c3d2c85ad26e4ec9', 'msg': '{"MODE": 2, "IV": "TW3agHgZJIMUWjb+9D1hwg==", "CipherText": "fdL9hbr0kHk+kazhr8i1Ng==", "ALG": 0}', 'alg': 'HMAC_SHA2'}
    >>> cipher.decrypt(ciphertext)
    b'My age is 42.'
    >>> ciphertext2 = cipher.encrypt(b'My age is 42.')
    >>> ciphertext2
    {'digest': '71ee405bdd51c507d960f7351efa186fa3f9c9a16164bfbe4420f6215b0f60cb', 'msg': '{"MODE": 2, "IV": "EL34abDraiSitGG60idAyA==", "CipherText": "jbPygJh+UnzGsucTCJyYew==", "ALG": 0}', 'alg': 'HMAC_SHA2'}
    >>> cipher.decrypt(ciphertext2)
    b'My age is 42.'
    >>> ciphertextAdditionalData = cipher.encrypt('Some network PDU.', additionalData=b'\x10\x11\x0a\x0b')
    >>> ciphertextAdditionalData
    {'digest': 'd781dbe8906b20a1d91f4bd28b57a84b93b0a520e08502a40208fd153bfb3206', 'msg': '{"MODE": 2, "IV": "pgg2Ik6ale5SjinjSpQejw==", "CipherText": "SsdRdwoX5yzdZNZnbbYbpUKlmCVbGVJPQl4/Bn9MBWo=", "ALG": 0}', 'alg': 'HMAC_SHA2'}
    >>> cipher.decrypt(ciphertextAdditionalData)
    Traceback (most recent call last):
      File "<stdin>", line 1, in <module>
      File "./charm/toolbox/symcrypto.py", line 233, in decrypt
        raise ValueError("Invalid mac. Your data was tampered with or your key is wrong")
    ValueError: Invalid mac. Your data was tampered with or your key is wrong
    >>> cipher.decrypt(ciphertextAdditionalData, additionalData='wrong data')
    Traceback (most recent call last):
      File "<stdin>", line 1, in <module>
      File "./charm/toolbox/symcrypto.py", line 233, in decrypt
        raise ValueError("Invalid mac. Your data was tampered with or your key is wrong")
    ValueError: Invalid mac. Your data was tampered with or your key is wrong
    >>> cipher.decrypt(ciphertextAdditionalData, additionalData=b'\x10\x11\x0a\x0b')
    b'Some network PDU.'
    >>>
    """
    def encrypt(self, msg, additionalData=''):
        """
        Encrypts a message in AEAD mode (Authenticated Encryption with Additional Data) using the superclass symmetric encryption parameters.
        The MAC is computed with both the ciphertext and additional data (and other cryptosystem parameters), but the additional data is not encrypted, nor
        saved within the ciphertext structure.

        Parameters
        ----------
        msg : str or byte str
            The message to be encrypted.
        additionalData : str or byte str, optional
            Additional data that will be MACed together with the ciphertext and algorithm; the additional message will not be encrypted.

        Returns
        -------
        dict
            Dictionary structure containing:
                msg: {'ALG': symmetric cryptosystem.
                      'MODE': symmetric encryption mode.
                      'IV': the IV for the encryption algorithm.
                      'CipherText': the padded ciphertext (padding according to PKCS 7).
                     }
                "alg": The HMAC algorithm.
                "digest": The MAC computed as MAC = HMAC(key, alg + additionalData + msg)

        Notes
        -----
        The IV is included in the computation of the MAC. In fact, all cipher parameters are included: the encryption function returns a JSON object from
        a dictionary composed of the cipher parameters (e.g., algorithm, mode, IV), and the ciphertext. The MAC function uses the whole JSON object/string
        to compute the MAC, prepended with the HMAC algorithm + additionalData.

        The MAC key is computed as sha2(b'Poor Mans Key Extractor" + key).
        """
        # warning only valid in the random oracle
        mac_key = sha2(b'Poor Mans Key Extractor'+self._key).digest()
        mac = MessageAuthenticator(mac_key)
        enc = super(AuthenticatedCryptoAbstraction, self).encrypt(msg)
        return mac.mac(enc, additionalData=additionalData)

    def decrypt(self, cipherText, additionalData=''):
        """
        Decrypts a ciphertext in AEAD mode (Authenticated Encryption with Additional Data) using the superclass symmetric encryption parameters.
        The MAC is computed with both the ciphertext and additional data (and other cryptosystem parameters), but the additional data is not encrypted, nor
        available within the ciphertext structure.

        Parameters
        ----------
        ciphertext : str or byte str
            The message to be decrypted.
        additionalData : str or byte str, optional
            Additional data that will be MACed together with the ciphertext and algorithm. This additional text must be in plaintext.

        Returns
        -------
        byte str
            The decrypted plaintext, if the ciphertext was successfuly authenticated. Raise exception otherwise.

        Raises
        ------
        ValueError
            If the MAC is invalid.

        Notes
        -----
        The IV is included in the computation of the MAC. In fact, all cipher parameters are included: the encryption function returns a JSON object from
        a dictionary composed of the cipher parameters (e.g., algorithm, mode, IV), and the ciphertext. The MAC function uses the whole JSON object/string
        to compute the MAC, prepended with the HMAC algorithm + additionalData.

        The MAC key is computed as sha2(b'Poor Mans Key Extractor" + key).
        """
        # warning only valid in the random oracle
        mac_key = sha2(b'Poor Mans Key Extractor'+self._key).digest()
        mac = MessageAuthenticator(mac_key)
        if not mac.verify(cipherText, additionalData=additionalData):
            raise ValueError("Invalid mac. Your data was tampered with or your key is wrong")
        else:
            return super(AuthenticatedCryptoAbstraction, self).decrypt(cipherText['msg'])
